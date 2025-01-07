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
# ログレベルの設定 (DEBUG_MODE 環境変数で制御)
########################
DEBUG_MODE = os.getenv("DEBUG_MODE", "False").lower() in ("true", "1", "t")
log_level = logging.DEBUG if DEBUG_MODE else logging.INFO

logging.basicConfig(
    level=log_level,
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
THREAD_ID = os.getenv("THREAD_ID")

if THREAD_ID is None:
    logging.error("THREAD_IDが設定されていません。環境変数を確認してください。")
    exit(1)  # ボットを終了
try:
    THREAD_ID = int(THREAD_ID)
except ValueError:
    logging.error("THREAD_IDが無効な値です。正しいチャンネルIDを設定してください。")
    exit(1)  # ボットを終了

########################
# リアクションIDの定義
########################
REACTIONS = {
    "b434": 1304690617405669376,  # <:b434:1304690617405669376>
    "b435": 1304690627723657267,  # <:b435:1304690627723657267>
    "b431": 1289782471197458495   # <:b431:1289782471197458495>
}

READ_LATER_REACTION_ID = REACTIONS["b434"]
FAVORITE_REACTION_ID = REACTIONS["b435"]
RANDOM_EXCLUDE_REACTION_ID = REACTIONS["b431"]
SPECIFIC_EXCLUDE_AUTHOR = 695096014482440244  # 特定投稿者

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
                reactions JSONB DEFAULT '{}',
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
# ヘルパー関数
########################
async def safe_fetch_message(channel, message_id):
    try:
        return await channel.fetch_message(message_id)
    except (discord.NotFound, discord.HTTPException):
        return None

async def ensure_message_in_db(message):
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT id FROM messages WHERE message_id = %s", (message.id,))
            row = cur.fetchone()
            if row:
                return
            # Initialize reactions with existing reactions from the message
            reactions_dict = {}
            for reaction in message.reactions:
                if reaction.custom_emoji:
                    emoji_id = reaction.emoji.id
                    if emoji_id:
                        users = [user.id async for user in reaction.users()]
                        reactions_dict[str(emoji_id)] = users
            reactions_json = json.dumps(reactions_dict)
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
                logging.debug(f"Added user_id={user_id} to reaction_id={emoji_id} for message_id={message_id}.")
            elif not add and user_id in user_list:
                user_list.remove(user_id)
                logging.debug(f"Removed user_id={user_id} from reaction_id={emoji_id} for message_id={message_id}.")

            reactions[str_emoji_id] = user_list
            cur.execute(
                "UPDATE messages SET reactions = %s WHERE message_id = %s",
                (json.dumps(reactions), message_id)
            )
            conn.commit()
            logging.debug(f"Updated reactions for message_id={message_id}: {reactions}")
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
    logging.debug(f"user_reacted: reaction_id={reaction_id}, user_id={user_id}, users={users}")
    return user_id in users

async def get_random_message(thread_id, filter_func=None, button_name="N/A"):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
            messages = cur.fetchall()

            for m in messages:
                if m['reactions'] is None:
                    m['reactions'] = {}
                elif isinstance(m['reactions'], str):
                    try:
                        m['reactions'] = json.loads(m['reactions']) or {}
                    except json.JSONDecodeError:
                        m['reactions'] = {}
                # デバッグ用のログ追加
                logging.debug(f"DB reactions for msg_id={m['message_id']}: {m['reactions']}")

            logging.info(f"[DEBUG] [{button_name}] get_random_message: total {len(messages)} messages before filter.")
            if filter_func:
                filtered = []
                for m in messages:
                    if filter_func(m):
                        filtered.append(m)
                logging.info(f"[DEBUG] [{button_name}] get_random_message: after filter -> {len(filtered)} messages remain.")
                messages = filtered

            if not messages:
                return None
            return random.choice(messages)
    except Error as e:
        logging.error(f"Error fetching random message: {e}")
        return None
    finally:
        release_db_connection(conn)

########################
# View クラス
########################
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
                # 連続投稿者除外しない → last_chosen_authors は更新してもしなくてもOK（ここでは残すが使わない）
                last_chosen_authors[user_id] = random_message['author_id']
                author_name = await self.get_author_name(random_message['author_id'])
                await interaction.response.send_message(
                   f"{interaction.user.mention} さんには、{author_name} さんが投稿したこの本がおすすめだよ！\n"
                    f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
                )
            else:
                await interaction.response.send_message(
                    f"{interaction.user.mention} さん、該当する投稿が見つかりませんでした。\n"
                    f"フィルター条件に一致する投稿が存在しないか、リアクションが不足しています。",
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

    # --- 青ボタン：ランダム ---
    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary, row=0, custom_id="blue_random")
    async def blue_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "blue_random"
        def filter_func(msg):
            # 自分の投稿は除外（連続投稿者除外はしないので削除）
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            logging.debug(f"[{button_name}] msg_id={msg['message_id']} PASSED.")
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

    # --- 青ボタン：あとで読む ---
    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary, row=0, custom_id="read_later")
    async def read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "blue_read_later"
        def filter_func(msg):
            if not user_reacted(msg, READ_LATER_REACTION_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: no b434 from user.")
                return False
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            logging.debug(f"[{button_name}] msg_id={msg['message_id']} PASSED.")
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

    # --- 青ボタン：お気に入り ---
    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary, row=0, custom_id="favorite")
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "blue_favorite"
        def filter_func(msg):
            # デバッグ用のログ追加
            logging.debug(f"DB reactions for msg_id={msg['message_id']}: {msg['reactions']}")

            if not user_reacted(msg, FAVORITE_REACTION_ID, interaction.user.id):
                logging.debug(f"Excluding msg_id={msg['message_id']}: reaction check failed, FAVORITE_REACTION_ID={FAVORITE_REACTION_ID}, user_id={interaction.user.id}, reactions={msg['reactions']}")
                return False
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            logging.debug(f"[{button_name}] msg_id={msg['message_id']} PASSED.")
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

    # --- 赤ボタン：ランダム ---
    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.danger, row=1, custom_id="red_random")
    async def red_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "red_random"
        def filter_func(msg):
            if user_reacted(msg, RANDOM_EXCLUDE_REACTION_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: user has b431.")
                return False
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            if msg['author_id'] == SPECIFIC_EXCLUDE_AUTHOR:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: specific exclude author.")
                return False
            logging.debug(f"[{button_name}] msg_id={msg['message_id']} PASSED.")
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

    # --- 赤ボタン：あとで読む ---
    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.danger, row=1, custom_id="conditional_read_later")
    async def conditional_read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "red_read_later"
        def filter_func(msg):
            if not user_reacted(msg, READ_LATER_REACTION_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: no b434 from user.")
                return False
            if user_reacted(msg, RANDOM_EXCLUDE_REACTION_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: user has b431.")
                return False
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
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

@bot.tree.command(name="check_reactions", description="特定のメッセージのリアクションを確認します。")
async def check_reactions(interaction: discord.Interaction, message_id: str):
    """特定のメッセージIDのリアクション情報を表示します。"""
    try:
        msg_id = int(message_id)
    except ValueError:
        await interaction.response.send_message("無効なメッセージIDです。", ephemeral=True)
        return

    conn = get_db_connection()
    if not conn:
        await interaction.response.send_message("データベース接続に失敗しました。", ephemeral=True)
        return

    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT reactions FROM messages WHERE message_id = %s", (msg_id,))
            row = cur.fetchone()
            if not row:
                await interaction.response.send_message("指定されたメッセージはデータベースに存在しません。", ephemeral=True)
                return
            reactions = row['reactions'] or {}
            if isinstance(reactions, str):
                try:
                    reactions = json.loads(reactions)
                except json.JSONDecodeError:
                    reactions = {}
            if not reactions:
                await interaction.response.send_message("このメッセージにはリアクションがありません。", ephemeral=True)
                return

            embed = discord.Embed(
                title=f"Message ID: {msg_id} のリアクション情報",
                color=0x00FF00
            )
            for emoji_id, user_ids in reactions.items():
                try:
                    emoji = bot.get_emoji(int(emoji_id))
                    if emoji:
                        emoji_str = str(emoji)
                    else:
                        emoji_str = f"Unknown Emoji ({emoji_id})"
                except ValueError:
                    emoji_str = f"Invalid Emoji ID ({emoji_id})"
                embed.add_field(name=emoji_str, value=f"{len(user_ids)} 人がリアクションしました。", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Error as e:
        logging.error(f"Error fetching reactions for message_id={msg_id}: {e}")
        await interaction.response.send_message("リアクション情報の取得中にエラーが発生しました。", ephemeral=True)
    finally:
        release_db_connection(conn)

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    logging.info(f"on_raw_reaction_add fired: emoji={payload.emoji}, user_id={payload.user_id}, message_id={payload.message_id}")
    # Ignore bot's own reactions
    if payload.user_id == bot.user.id:
        logging.debug("Reaction added by the bot itself; ignoring.")
        return
    # Check if the emoji is one of the target reactions
    if payload.emoji.id not in REACTIONS.values():
        logging.debug(f"Ignoring reaction with emoji_id={payload.emoji.id} not in target reactions.")
        return
    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        logging.info("channel is None, cannot process reaction.")
        return
    message = await safe_fetch_message(channel, payload.message_id)
    if message is None:
        logging.info(f"message_id={payload.message_id} not found in channel.")
        return
    await ensure_message_in_db(message)
    await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=True)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    logging.info(f"on_raw_reaction_remove fired: emoji={payload.emoji}, user_id={payload.user_id}, message_id={payload.message_id}")
    # Ignore bot's own reactions
    if payload.user_id == bot.user.id:
        logging.debug("Reaction removed by the bot itself; ignoring.")
        return
    # Check if the emoji is one of the target reactions
    if payload.emoji.id not in REACTIONS.values():
        logging.debug(f"Ignoring reaction removal with emoji_id={payload.emoji.id} not in target reactions.")
        return
    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        logging.info("channel is None, cannot process reaction removal.")
        return
    message = await safe_fetch_message(channel, payload.message_id)
    if message is None:
        logging.info(f"message_id={payload.message_id} not found in channel.")
        return
    await ensure_message_in_db(message)
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
            # Fetch reactions for the message
            reactions_dict = {}
            for reaction in message.reactions:
                if reaction.custom_emoji:
                    emoji_id = reaction.emoji.id
                    if emoji_id:
                        try:
                            users = [user.id async for user in reaction.users()]
                        except discord.HTTPException as e:
                            logging.error(f"Error fetching users for reaction {emoji_id} in message {message.id}: {e}")
                            users = []
                        reactions_dict[str(emoji_id)] = users
            reactions_json = json.dumps(reactions_dict)
            data.append((message.id, THREAD_ID, message.author.id, reactions_json, message.content))
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (message_id) DO UPDATE SET content = EXCLUDED.content, reactions = EXCLUDED.reactions
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
