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
# .envの環境変数を読み込む
# ------------------------------------------------
load_dotenv()

# ログ出力の設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
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
    """
    DB接続プールから接続を取得する。
    """
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
    """
    使い終わったDB接続をプールに返却する。
    """
    try:
        if db_pool and conn:
            db_pool.putconn(conn)
    except Error as e:
        logging.error(f"Error releasing database connection: {e}")

# ------------------------------------------------
# テーブルを初期化する（存在しない場合は作成）
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
# Intent設定
# ------------------------------------------------
# リアクションやメッセージコンテンツ等を取得できるように設定する。
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
READ_LATER_REACTION_ID = 1304690617405669376     # <:b434:...>
FAVORITE_REACTION_ID = 1304690627723657267       # <:b435:...>
RANDOM_EXCLUDE_REACTION_ID = 1289782471197458495 # <:b431:...>
SPECIFIC_EXCLUDE_AUTHOR = 695096014482440244     # 除外したい投稿者ID

# ユーザーごとに前回選ばれた投稿者IDを記録し、連続選出を防ぐ
last_chosen_authors = {}

# ------------------------------------------------
# メッセージを安全に取得するヘルパー関数
# ------------------------------------------------
async def safe_fetch_message(channel, message_id):
    """
    指定したチャンネルのmessage_idのメッセージを安全に取得する。
    取得できない場合はNoneを返す。
    """
    try:
        return await channel.fetch_message(message_id)
    except (discord.NotFound, discord.HTTPException):
        return None

# ------------------------------------------------
# DBにメッセージがなければ新規挿入する関数
# ------------------------------------------------
def ensure_message_in_db(message):
    """
    DBに登録されていないメッセージの場合、
    その場でINSERTする（リアクション管理のため）。
    """
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT id FROM messages WHERE message_id = %s", (message.id,))
            row = cur.fetchone()
            if row:
                # 既にDBに登録済みなら何もしない
                return

            # 登録されていない場合はINSERT
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
    """
    'messages'テーブルのreactions(JSONB)を更新する。
    カラムに保持した辞書から指定のユーザーIDを追加/削除する。
    """
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT reactions FROM messages WHERE message_id = %s", (message_id,))
            row = cur.fetchone()
            if not row:
                # DBに該当メッセージがないなら何もしない
                logging.info(f"No row found for message_id={message_id}, skipping reaction update.")
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
# ユーザーが特定のカスタム絵文字にリアクションしているか判定する関数
# ------------------------------------------------
def user_reacted(msg, reaction_id, user_id):
    """
    メッセージのreactions(JSON)を読み込み、reaction_idに対してuser_idが含まれているか確認する。
    """
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
    """
    thread_idに紐づくmessagesテーブル上の投稿をすべて取得し、
    filter_funcの条件に合うものだけからランダムに1つ返す。
    """
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
            messages = cur.fetchall()

            # reactionsが文字列の場合、辞書型にパースする
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
        """
        author_idからユーザー名を取得するヘルパー関数。
        存在しないユーザーの場合は'Unknown User'を返す。
        """
        user = bot.get_user(author_id)
        if user is None:
            try:
                user = await bot.fetch_user(author_id)
            except discord.NotFound:
                user = None
        return user.display_name if user and user.display_name else (user.name if user else "Unknown User")

    async def handle_selection(self, interaction, random_message, user_id):
        """
        ランダムで選ばれたメッセージをユーザーに送信する。
        """
        try:
            if random_message:
                # 連続して同じ投稿者を除外するため、今回選ばれたauthor_idを記録
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
            # 選択後にパネルを再表示（任意）
            await send_panel(interaction.channel)

    async def get_and_handle_random_message(self, interaction, filter_func):
        """
        指定したフィルタリング関数を使ってランダムメッセージを選び、handle_selectionで処理する。
        """
        random_message = await get_random_message(THREAD_ID, filter_func)
        await self.handle_selection(interaction, random_message, interaction.user.id)

    # --------------------------------------------------------------------
    # 【青ボタン：ランダム】
    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary, row=0, custom_id="blue_random")
    async def blue_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        全体の投稿からランダムで1つ選択。
        除外条件:
        1) 自分の投稿
        2) 特定の投稿者(SPECIFIC_EXCLUDE_AUTHOR)
        3) 連続して同じ投稿者
        """
        def filter_func(msg):
            if msg['author_id'] == interaction.user.id:
                return False
            if msg['author_id'] == SPECIFIC_EXCLUDE_AUTHOR:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func)

    # --------------------------------------------------------------------
    # 【青ボタン：あとで読む】
    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary, row=0, custom_id="read_later")
    async def read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        ボタンを押したユーザーが<:b434:...>を付けた投稿からランダムで1つ選択。
        除外条件:
        1) 自分の投稿
        2) 特定の投稿者
        3) 連続して同じ投稿者
        """
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

    # --------------------------------------------------------------------
    # 【青ボタン：お気に入り】
    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary, row=0, custom_id="favorite")
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        ボタンを押したユーザーが<:b435:...>を付けた投稿からランダムで1つ選択。
        除外条件:
        1) 自分の投稿
        2) 特定の投稿者
        3) 連続して同じ投稿者
        """
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

    # --------------------------------------------------------------------
    # 【赤ボタン：ランダム】
    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.danger, row=1, custom_id="red_random")
    async def red_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        ユーザーが<:b431:...>を付けた投稿を除外、それ以外からランダムで1つ選択。
        除外条件:
        1) 自分の投稿
        2) 特定の投稿者
        3) 連続して同じ投稿者
        """
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

    # --------------------------------------------------------------------
    # 【赤ボタン：あとで読む】
    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.danger, row=1, custom_id="conditional_read_later")
    async def conditional_read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        ボタンを押したユーザーが<:b434:...>を付けた投稿の中から、
        ボタンを押したユーザーが<:b431:...>を付けた投稿を除外して1つランダム選択。
        さらに自分の投稿・特定投稿者・連続同じ投稿者は除外。
        """
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
    """
    パネル(ボタン付きEmbed)を指定チャンネルに送信する。
    既存のパネルがあれば削除してから再送信。
    """
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
    """
    パネルの機能説明を記載したEmbedを作成し、返す。
    """
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
# コントロールパネルを表示
# ------------------------------------------------
@bot.tree.command(name="panel", description="パネルを表示します。")
async def panel(interaction: discord.Interaction):
    """
    /panel コマンド
    """
    channel = interaction.channel
    if channel:
        await interaction.response.send_message("パネルを表示します！", ephemeral=True)
        await send_panel(channel)
    else:
        await interaction.response.send_message("エラー: チャンネルが取得できませんでした。", ephemeral=True)

# ------------------------------------------------
# リアクションイベント：追加
# ------------------------------------------------
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """
    リアクションが追加されたら、該当メッセージがDBにない場合はINSERTし、
    カスタム絵文字のみDB更新を行う（標準絵文字は対象外）。
    必要に応じて標準絵文字も拾いたい場合は if を削除する。
    """
    logging.info(f"on_raw_reaction_add fired: emoji={payload.emoji}, user={payload.user_id}")

    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        logging.info("channel is None, cannot process reaction.")
        return

    message = await safe_fetch_message(channel, payload.message_id)
    if message is None:
        logging.info(f"message_id={payload.message_id} not found in channel.")
        return

    # DBにまだ登録されていなかった場合、ここでINSERTしておく
    ensure_message_in_db(message)

    # カスタム絵文字のみ扱う（標準絵文字を含めたいならここのifを削除 or 条件変更）
    if payload.emoji.is_custom_emoji():
        await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=True)

# ------------------------------------------------
# リアクションイベント：削除
# ------------------------------------------------
@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    """
    リアクションが削除されたら、同様にメッセージがDBに無い場合はINSERTしてから、
    カスタム絵文字のみDB更新を行う。
    """
    logging.info(f"on_raw_reaction_remove fired: emoji={payload.emoji}, user={payload.user_id}")

    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        logging.info("channel is None, cannot process reaction removal.")
        return

    message = await safe_fetch_message(channel, payload.message_id)
    if message is None:
        logging.info(f"message_id={payload.message_id} not found in channel.")
        return

    # DBにまだ登録されていなかった場合、ここでINSERT
    ensure_message_in_db(message)

    if payload.emoji.is_custom_emoji():
        await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=False)

# ------------------------------------------------
# Botが起動したときに呼ばれるイベント
# ------------------------------------------------
@bot.event
async def on_ready():
    """
    Botが準備完了したときに呼ばれる。
    """
    logging.info(f"Bot is online! {bot.user}")
    # 定期タスクを開始
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
    """
    60分ごとにメッセージをDBに保存するタスク。
    """
    await save_all_messages_to_db()

async def save_all_messages_to_db():
    """
    THREAD_IDで指定したスレッドのメッセージを取得し、DBにまとめて保存する。
    """
    channel = bot.get_channel(THREAD_ID)
    if channel:
        try:
            limit_count = 100  # 一度に取得するメッセージ数
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
    """
    取得した複数のメッセージをまとめてデータベースにINSERTまたはUPDATEする。
    """
    conn = get_db_connection()
    if not conn or not messages:
        return
    try:
        data = []
        for message in messages:
            # とりあえずreactionsは空JSONで登録し、on_raw_reaction_add等で更新していく
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
# Botを起動する
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
