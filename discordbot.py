import discord
from discord.ext import commands, tasks
import random
import asyncio
import logging
import psycopg2
from psycopg2 import pool, Error
from psycopg2.extras import DictCursor
from dotenv import load_dotenv
import os
import json

########################
# .env 環境変数読み込み
########################
load_dotenv()

########################
# ログレベルを DEBUG に
########################
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

########################
# DB 情報
########################
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

########################
# DB接続プール
########################
try:
    db_pool = pool.SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        dsn=DATABASE_URL,
        sslmode='require'
    )
    logging.info("Database connection pool initialized.")
except Error as e:
    logging.error(f"Database connection pool initialization error: {e}")
    db_pool = None

def get_db_connection():
    try:
        if db_pool:
            return db_pool.getconn()
        else:
            raise Error("Database connection pool is not initialized.")
    except Error as e:
        logging.error(f"Error getting database connection: {e}")
        return None

def release_db_connection(conn):
    try:
        if db_pool and conn:
            db_pool.putconn(conn)
    except Error as e:
        logging.error(f"Error releasing database connection: {e}")

def initialize_db():
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                message_id BIGINT NOT NULL UNIQUE,
                thread_id BIGINT NOT NULL,
                author_id BIGINT NOT NULL,
                reactions JSONB,
                content TEXT
            )
            """)
            conn.commit()
        logging.info("Database initialized successfully.")
    except Error as e:
        logging.error(f"Error initializing tables: {e}")
    finally:
        release_db_connection(conn)

initialize_db()

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.reactions = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

########################
# 定数の定義
########################
THREAD_ID = 1288407362318893109
READ_LATER_REACTION_ID = 1304690617405669376     
FAVORITE_REACTION_ID = 1304690627723657267       
RANDOM_EXCLUDE_REACTION_ID = 1289782471197458495 
SPECIFIC_EXCLUDE_AUTHOR = 695096014482440244     

last_chosen_authors = {}

########################
# ヘルパー関数
########################
async def safe_fetch_message(channel, message_id):
    try:
        return await channel.fetch_message(message_id)
    except (discord.NotFound, discord.HTTPException):
        return None

def ensure_message_in_db(message):
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT id FROM messages WHERE message_id = %s", (message.id,))
            row = cur.fetchone()
            if row:
                return

            reactions_json = json.dumps({})
            cur.execute(
                """
                INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (message.id, message.channel.id, message.author.id, reactions_json, message.content)
            )
            conn.commit()
            logging.info(f"Inserted new message into DB (message_id={message.id}).")
    except Error as e:
        logging.error(f"Error ensuring message in DB: {e}")
    finally:
        release_db_connection(conn)

async def update_reactions_in_db(message_id, emoji_id, user_id, add=True):
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT reactions FROM messages WHERE message_id = %s", (message_id,))
            row = cur.fetchone()
            if not row:
                logging.info(f"No row found for message_id={message_id}, skip reaction update.")
                return

            reactions = row['reactions'] or {}
            if isinstance(reactions, str):
                try:
                    reactions = json.loads(reactions)
                except json.JSONDecodeError:
                    reactions = {}

            str_emoji_id = str(emoji_id)
            user_list = reactions.get(str_emoji_id, [])

            if add and user_id not in user_list:
                user_list.append(user_id)
            elif not add and user_id in user_list:
                user_list.remove(user_id)

            reactions[str_emoji_id] = user_list
            cur.execute(
                "UPDATE messages SET reactions = %s WHERE message_id = %s",
                (json.dumps(reactions), message_id)
            )
            conn.commit()
    except Error as e:
        logging.error(f"Error updating reactions in DB: {e}")
    finally:
        release_db_connection(conn)

def user_reacted(msg, reaction_id, user_id):
    reaction_data = msg.get('reactions', {})
    if isinstance(reaction_data, str):
        try:
            reaction_data = json.loads(reaction_data)
        except json.JSONDecodeError:
            reaction_data = {}
    users = reaction_data.get(str(reaction_id), [])
    return user_id in users

def get_authors_count(messages):
    """投稿全体で何人の著者がいるかを調べる"""
    authors = set()
    for m in messages:
        authors.add(m['author_id'])
    return len(authors)

###############################
# get_random_message改変
###############################
async def get_random_message(thread_id, filter_func=None, button_name="N/A"):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
            messages = cur.fetchall()

            # JSONパース
            for m in messages:
                if m['reactions'] is None:
                    m['reactions'] = {}
                elif isinstance(m['reactions'], str):
                    try:
                        m['reactions'] = json.loads(m['reactions']) or {}
                    except json.JSONDecodeError:
                        m['reactions'] = {}

            logging.info(f"[DEBUG] [{button_name}] get_random_message: total {len(messages)} messages before filter.")

            if not messages:
                return None

            # まず、著者数をざっくり数える
            authors_count = get_authors_count(messages)
            skip_consecutive_author_check = (authors_count < 3)  # 例：3人未満なら連続投稿者除外をスキップ

            filtered = []
            for m in messages:
                if filter_func:
                    if filter_func(m, skip_consecutive_author_check):
                        filtered.append(m)
                else:
                    filtered.append(m)

            logging.info(f"[DEBUG] [{button_name}] get_random_message: after filter -> {len(filtered)} messages remain.")

            if not filtered:
                return None

            return random.choice(filtered)
    except Error as e:
        logging.error(f"Error fetching random message: {e}")
        return None
    finally:
        release_db_connection(conn)

###############################
# ボタンView
###############################
class CombinedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def get_author_name(self, author_id):
        user = bot.get_user(author_id)
        if user is None:
            try:
                user = await bot.fetch_user(author_id)
            except discord.NotFound:
                user = None
        return user.display_name if user and user.display_name else (user.name if user else "Unknown User")

    async def handle_selection(self, interaction, random_message, user_id):
        try:
            if random_message:
                last_chosen_authors[user_id] = random_message['author_id']
                author_name = await self.get_author_name(random_message['author_id'])
                await interaction.response.send_message(
                     f"{interaction.user.mention} さんには、{author_name} さんが投稿したこの本がおすすめだよ！\n"
                    f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"{interaction.user.mention} さん、該当する投稿が見つかりませんでした。",
                    ephemeral=True
                )
        except Exception as e:
            logging.error(f"Error handling selection: {e}")
            await interaction.response.send_message(
                f"{interaction.user.mention} さん、エラーが発生しました。しばらくしてからお試しください。",
                ephemeral=True
            )
        finally:
            await send_panel(interaction.channel)

    async def get_and_handle_random_message(self, interaction, filter_func, button_name="N/A"):
        random_message = await get_random_message(THREAD_ID, filter_func=filter_func, button_name=button_name)
        await self.handle_selection(interaction, random_message, interaction.user.id)

    #
    # 青ボタン：ランダム (例: 連続投稿者除外を緩和)
    #
    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary, row=0, custom_id="blue_random")
    async def blue_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "blue_random"
        def filter_func(msg, skip_consecutive_author_check=False):
            # 自分の投稿を除外
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False

            # 連続投稿者除外 ただしskipフラグがTrueならスキップ
            if not skip_consecutive_author_check:
                if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                    logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same author as previous.")
                    return False

            logging.debug(f"[{button_name}] msg_id={msg['message_id']} PASSED.")
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

    #
    # 他ボタンも同様に、連続投稿者除外を緩和
    #
    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary, row=0, custom_id="read_later")
    async def read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "blue_read_later"
        def filter_func(msg, skip_consecutive_author_check=False):
            if not user_reacted(msg, READ_LATER_REACTION_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: no b434.")
                return False
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            if not skip_consecutive_author_check:
                if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                    logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same author as previous.")
                    return False
            logging.debug(f"[{button_name}] msg_id={msg['message_id']} PASSED.")
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary, row=0, custom_id="favorite")
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "blue_favorite"
        def filter_func(msg, skip_consecutive_author_check=False):
            if not user_reacted(msg, FAVORITE_REACTION_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: no b435.")
                return False
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            if not skip_consecutive_author_check:
                if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                    logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same author as previous.")
                    return False
            logging.debug(f"[{button_name}] msg_id={msg['message_id']} PASSED.")
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

    #
    # 赤ボタン：ランダム (b431を除外 + 特定投稿者除外 + 自分の投稿除外)
    #
    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.danger, row=1, custom_id="red_random")
    async def red_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "red_random"
        def filter_func(msg, skip_consecutive_author_check=False):
            if user_reacted(msg, RANDOM_EXCLUDE_REACTION_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: user has b431.")
                return False
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            if msg['author_id'] == SPECIFIC_EXCLUDE_AUTHOR:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: specific exclude author.")
                return False
            if not skip_consecutive_author_check:
                if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                    logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same author as previous.")
                    return False
            logging.debug(f"[{button_name}] msg_id={msg['message_id']} PASSED.")
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

    #
    # 赤ボタン：あとで読む
    #
    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.danger, row=1, custom_id="conditional_read_later")
    async def conditional_read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "red_read_later"
        def filter_func(msg, skip_consecutive_author_check=False):
            if not user_reacted(msg, READ_LATER_REACTION_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: no b434.")
                return False
            if user_reacted(msg, RANDOM_EXCLUDE_REACTION_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: user has b431.")
                return False
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            if not skip_consecutive_author_check:
                if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                    logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same author as previous.")
                    return False
            logging.debug(f"[{button_name}] msg_id={msg['message_id']} PASSED.")
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

current_panel_message_id = None

async def send_panel(channel):
    global current_panel_message_id
    if current_panel_message_id:
        try:
            panel_message = await channel.fetch_message(current_panel_message_id)
            await panel_message.delete()
            logging.info(f"Deleted previous panel message with ID {current_panel_message_id}.")
        except discord.NotFound:
            logging.warning(f"Previous panel message with ID {current_panel_message_id} not found.")
        except discord.HTTPException as e:
            logging.error(f"Error deleting panel message: {e}")

    embed = create_panel_embed()
    view = CombinedView()
    try:
        sent_message = await channel.send(embed=embed, view=view)
        current_panel_message_id = sent_message.id
        logging.info(f"Sent new panel message with ID {current_panel_message_id}.")
    except discord.HTTPException as e:
        logging.error(f"Error sending panel message: {e}")

def create_panel_embed():
    embed = discord.Embed(
        title="🎯ｴﾛ漫画ﾙｰﾚｯﾄ",
        description=(
            "botがｴﾛ漫画を選んでくれるよ！\n\n"
            "🔵：自分の<:b431:1289782471197458495>を除外しない\n"
            "🔴：自分の<:b431:1289782471197458495>を除外する\n\n"
            "ランダム：全体から選ぶ\n"
            "あとで読む：<:b434:1304690617405669376>を付けた投稿から選ぶ\n"
            "お気に入り：<:b435:1304690627723657267>を付けた投稿から選ぶ"
        ),
        color=0xFF69B4
    )
    return embed

@bot.tree.command(name="panel", description="パネルを表示します。")
async def panel(interaction: discord.Interaction):
    channel = interaction.channel
    if channel:
        await interaction.response.send_message("パネルを表示します！", ephemeral=True)
        await send_panel(channel)
    else:
        await interaction.response.send_message("エラー: チャンネルが取得できませんでした。", ephemeral=True)

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    logging.info(f"on_raw_reaction_add fired: emoji={payload.emoji}, user_id={payload.user_id}, message_id={payload.message_id}")
    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        logging.info("channel is None, cannot process reaction.")
        return
    message = await safe_fetch_message(channel, payload.message_id)
    if message is None:
        logging.info(f"message_id={payload.message_id} not found in channel.")
        return
    ensure_message_in_db(message)
    await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=True)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    logging.info(f"on_raw_reaction_remove fired: emoji={payload.emoji}, user_id={payload.user_id}, message_id={payload.message_id}")
    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        logging.info("channel is None, cannot process reaction removal.")
        return
    message = await safe_fetch_message(channel, payload.message_id)
    if message is None:
        logging.info(f"message_id={payload.message_id} not found in channel.")
        return
    ensure_message_in_db(message)
    await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=False)

@bot.event
async def on_ready():
    logging.info(f"Bot is online! {bot.user}")
    save_all_messages_to_db_task.start()
    try:
        synced = await bot.tree.sync()
        logging.info(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        logging.error(f"Error syncing slash commands: {e}")

@tasks.loop(minutes=60)
async def save_all_messages_to_db_task():
    await save_all_messages_to_db()

async def save_all_messages_to_db():
    channel = bot.get_channel(THREAD_ID)
    if channel:
        try:
            limit_count = 200
            messages = []
            async for message in channel.history(limit=limit_count):
                messages.append(message)
            if messages:
                await bulk_save_messages_to_db(messages)
            logging.info(f"Saved up to {limit_count} messages to the database.")
        except discord.HTTPException as e:
            logging.error(f"Error fetching message history: {e}")
    else:
        logging.error("指定したTHREAD_IDのチャンネルが見つかりませんでした。")

async def bulk_save_messages_to_db(messages):
    conn = get_db_connection()
    if not conn or not messages:
        return
    try:
        data = []
        for message in messages:
            reactions_json = json.dumps({})
            data.append((message.id, THREAD_ID, message.author.id, reactions_json, message.content))

        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (message_id) DO UPDATE SET content = EXCLUDED.content
            """, data)
            conn.commit()
        logging.info(f"Bulk inserted or updated {len(messages)} messages.")
    except Error as e:
        logging.error(f"Error during bulk insert/update: {e}")
    finally:
        release_db_connection(conn)

if DISCORD_TOKEN:
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logging.error(f"Error starting the bot: {e}")
        if db_pool:
            db_pool.closeall()
            logging.info("Closed all database connections.")
else:
    logging.error("DISCORD_TOKENが設定されていません。")
