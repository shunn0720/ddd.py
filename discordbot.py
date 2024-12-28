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

# ------------------------------------------------
# .envの環境変数を読み込み
# ------------------------------------------------
load_dotenv()

# ログ出力の設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),  # ファイル出力
        logging.StreamHandler()          # コンソール出力
    ]
)

# ------------------------------------------------
# 環境変数からDB接続情報・トークンを取得
# ------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# ------------------------------------------------
# DB接続プールの初期化
# ------------------------------------------------
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

# ------------------------------------------------
# DB接続を取得するヘルパー関数
# ------------------------------------------------
def get_db_connection():
    try:
        if db_pool:
            return db_pool.getconn()
        else:
            raise Error("Database connection pool is not initialized.")
    except Error as e:
        logging.error(f"Error getting database connection: {e}")
        return None

# ------------------------------------------------
# DB接続をリリースするヘルパー関数
# ------------------------------------------------
def release_db_connection(conn):
    try:
        if db_pool and conn:
            db_pool.putconn(conn)
    except Error as e:
        logging.error(f"Error releasing database connection: {e}")

# ------------------------------------------------
# テーブルを初期化（存在しない場合は作成）
# ------------------------------------------------
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

# ------------------------------------------------
# インテントの設定（on_raw_reaction_addが呼ばれるように）
# ------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.reactions = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------------------------------------
# 定数の定義
# ------------------------------------------------
THREAD_ID = 1288407362318893109  # メッセージを収集するスレッド
READ_LATER_REACTION_ID = 1304690617405669376     # <:b434:...> のID
FAVORITE_REACTION_ID = 1304690627723657267       # <:b435:...> のID
RANDOM_EXCLUDE_REACTION_ID = 1289782471197458495 # <:b431:...> のID
SPECIFIC_EXCLUDE_AUTHOR = 695096014482440244     # 除外したい投稿者のID

# ユーザーごとに前回選ばれた投稿者IDを追跡し、連続投稿を避けるために使用
last_chosen_authors = {}

# ------------------------------------------------
# メッセージを安全に取得するヘルパー関数
# ------------------------------------------------
async def safe_fetch_message(channel, message_id):
    try:
        return await channel.fetch_message(message_id)
    except (discord.NotFound, discord.HTTPException):
        return None

# ------------------------------------------------
# DBにメッセージが無い場合INSERTする関数
# ------------------------------------------------
def ensure_message_in_db(message):
    """
    DBに未登録の場合、その場でINSERTする。
    """
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT id FROM messages WHERE message_id = %s", (message.id,))
            row = cur.fetchone()
            if row:
                # 既に登録済みならスキップ
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

# ------------------------------------------------
# メッセージのreactionsを更新する関数
# ------------------------------------------------
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

# ------------------------------------------------
# ユーザーが特定のカスタム絵文字にリアクションしているか判定
# ------------------------------------------------
def user_reacted(msg, reaction_id, user_id):
    reaction_data = msg.get('reactions', {})
    if isinstance(reaction_data, str):
        try:
            reaction_data = json.loads(reaction_data)
        except json.JSONDecodeError:
            reaction_data = {}
    users = reaction_data.get(str(reaction_id), [])
    return user_id in users

# ------------------------------------------------
# 指定したthread_idのメッセージからランダムに選ぶ関数
# ------------------------------------------------
async def get_random_message(thread_id, filter_func=None):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
            messages = cur.fetchall()
            # reactions が文字列の場合は辞書型に変換
            for m in messages:
                if m['reactions'] is None:
                    m['reactions'] = {}
                elif isinstance(m['reactions'], str):
                    try:
                        m['reactions'] = json.loads(m['reactions']) or {}
                    except json.JSONDecodeError:
                        m['reactions'] = {}

            if filter_func:
                messages = [m for m in messages if filter_func(m)]
            if not messages:
                return None
            return random.choice(messages)
    except Error as e:
        logging.error(f"Error fetching random message: {e}")
        return None
    finally:
        release_db_connection(conn)

# ------------------------------------------------
# ボタンが集約されたViewクラス
# ------------------------------------------------
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
                    f"{interaction.user.mention} さん、こちらはいかがでしょう？（投稿者：**{author_name}**）\n"
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

    async def get_and_handle_random_message(self, interaction, filter_func):
        random_message = await get_random_message(THREAD_ID, filter_func)
        await self.handle_selection(interaction, random_message, interaction.user.id)

    # 【青ボタン：ランダム】
    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary, row=0, custom_id="blue_random")
    async def blue_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        def filter_func(msg):
            if msg['author_id'] == interaction.user.id:
                return False
            if msg['author_id'] == SPECIFIC_EXCLUDE_AUTHOR:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func)

    # 【青ボタン：あとで読む】
    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary, row=0, custom_id="read_later")
    async def read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        def filter_func(msg):
            if not user_reacted(msg, READ_LATER_REACTION_ID, interaction.user.id):
                return False
            if msg['author_id'] == interaction.user.id:
                return False
            if msg['author_id'] == SPECIFIC_EXCLUDE_AUTHOR:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func)

    # 【青ボタン：お気に入り】
    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary, row=0, custom_id="favorite")
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        def filter_func(msg):
            if not user_reacted(msg, FAVORITE_REACTION_ID, interaction.user.id):
                return False
            if msg['author_id'] == interaction.user.id:
                return False
            if msg['author_id'] == SPECIFIC_EXCLUDE_AUTHOR:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func)

    # 【赤ボタン：ランダム】
    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.danger, row=1, custom_id="red_random")
    async def red_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        def filter_func(msg):
            if user_reacted(msg, RANDOM_EXCLUDE_REACTION_ID, interaction.user.id):
                return False
            if msg['author_id'] == interaction.user.id:
                return False
            if msg['author_id'] == SPECIFIC_EXCLUDE_AUTHOR:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func)

    # 【赤ボタン：あとで読む】
    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.danger, row=1, custom_id="conditional_read_later")
    async def conditional_read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        def filter_func(msg):
            if not user_reacted(msg, READ_LATER_REACTION_ID, interaction.user.id):
                return False
            if user_reacted(msg, RANDOM_EXCLUDE_REACTION_ID, interaction.user.id):
                return False
            if msg['author_id'] == interaction.user.id:
                return False
            if msg['author_id'] == SPECIFIC_EXCLUDE_AUTHOR:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func)

# ------------------------------------------------
# パネルメッセージを送る関数
# ------------------------------------------------
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

# ------------------------------------------------
# スラッシュコマンド：/panel
# ------------------------------------------------
@bot.tree.command(name="panel", description="パネルを表示します。")
async def panel(interaction: discord.Interaction):
    channel = interaction.channel
    if channel:
        await interaction.response.send_message("パネルを表示します！", ephemeral=True)
        await send_panel(channel)
    else:
        await interaction.response.send_message("エラー: チャンネルが取得できませんでした。", ephemeral=True)

# ------------------------------------------------
# リアクションイベント：追加 (標準/カスタム両方拾う)
# ------------------------------------------------
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

    # DBに未登録ならその場でINSERT
    ensure_message_in_db(message)

    # 全てのリアクション（標準/カスタム）を拾う
    await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=True)

# ------------------------------------------------
# リアクションイベント：削除 (標準/カスタム両方拾う)
# ------------------------------------------------
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

# ------------------------------------------------
# Botが起動したとき
# ------------------------------------------------
@bot.event
async def on_ready():
    logging.info(f"Bot is online! {bot.user}")
    save_all_messages_to_db_task.start()
    try:
        synced = await bot.tree.sync()
        logging.info(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        logging.error(f"Error syncing slash commands: {e}")

# ------------------------------------------------
# 定期タスク：DBにメッセージを保存
# ------------------------------------------------
@tasks.loop(minutes=60)
async def save_all_messages_to_db_task():
    await save_all_messages_to_db()

async def save_all_messages_to_db():
    channel = bot.get_channel(THREAD_ID)
    if channel:
        try:
            limit_count = 100
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
            # まだリアクション情報は空JSONとして挿入し、on_raw_reaction_addで更新
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

# ------------------------------------------------
# Bot起動
# ------------------------------------------------
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
