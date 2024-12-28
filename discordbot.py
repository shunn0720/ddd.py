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
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

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
# テーブルが無い場合は作成する
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
# Discord Botのセットアップ
# ------------------------------------------------
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True  # メンバー情報が必要な場合はTrue

bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------------------------------------
# 定数の定義
# ------------------------------------------------
THREAD_ID = 1288407362318893109
READ_LATER_REACTION_ID = 1304690617405669376
FAVORITE_REACTION_ID = 1304690627723657267
RANDOM_EXCLUDE_REACTION_ID = 1289782471197458495
SPECIFIC_EXCLUDE_AUTHOR = 695096014482440244

# ユーザーごとに前回選ばれた作者IDを追跡するための辞書
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
# DB上のreactionsを更新する関数
# ------------------------------------------------
async def update_reactions_in_db(message_id, emoji_id, user_id, add=True):
    """
    'messages'テーブルのreactionsカラム( JSON形式 )を更新し、
    指定された絵文字にユーザーのIDを追加または削除する。
    """
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT reactions FROM messages WHERE message_id = %s", (message_id,))
            row = cur.fetchone()
            if not row:
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
# ユーザーが指定の絵文字でリアクションしているか判定する関数
# ------------------------------------------------
def user_reacted(msg, reaction_id, user_id):
    """
    メッセージmsgのreactionsにおいて、user_idがreaction_idでリアクションしたかどうかを確認する。
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
# 指定したスレッドIDからランダムにメッセージを取得する関数
# ------------------------------------------------
async def get_random_message(thread_id, filter_func=None):
    """
    データベースからthread_idに紐づくメッセージをすべて取得し、
    filter_funcを適用した上でランダムに1件返す。
    """
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
            messages = cur.fetchall()

            # reactionsカラムが文字列の場合は辞書型に変換
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
        ランダムで選ばれたメッセージをユーザーに返答として送信する。
        """
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
        """
        フィルタリング関数(filter_func)を適用してランダムメッセージを選び、
        その結果をhandle_selectionに渡す。
        """
        random_message = await get_random_message(THREAD_ID, filter_func)
        await self.handle_selection(interaction, random_message, interaction.user.id)

    # --------------------------------------------
    # ボタン：ランダム（青）
    # --------------------------------------------
    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary, row=0, custom_id="blue_random")
    async def blue_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        青い「ランダム」ボタンが押された時の処理。
        「自分の投稿」「特定の投稿者」「直前と同じ投稿者」を除外してランダムに選ぶ。
        """
        def filter_func(msg):
            # 自分の投稿を除外
            if msg['author_id'] == interaction.user.id:
                return False
            # 特定の投稿者を除外
            if msg['author_id'] == SPECIFIC_EXCLUDE_AUTHOR:
                return False
            # 前回と同じ作者を除外
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func)

    # --------------------------------------------
    # ボタン：あとで読む（青）
    # --------------------------------------------
    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary, row=0, custom_id="read_later")
    async def read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        青い「あとで読む」ボタンが押された時の処理。
        「<:b434:1304690617405669376> を付けた投稿」かつ
        「自分の投稿」「特定の投稿者」「直前と同じ投稿者」を除外してランダムに選ぶ。
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

    # --------------------------------------------
    # ボタン：お気に入り（青）
    # --------------------------------------------
    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary, row=0, custom_id="favorite")
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        青い「お気に入り」ボタンが押された時の処理。
        「<:b435:1304690627723657267> を付けた投稿」かつ
        「自分の投稿」「特定の投稿者」「直前と同じ投稿者」を除外してランダムに選ぶ。
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

    # --------------------------------------------
    # ボタン：ランダム（赤）
    # --------------------------------------------
    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.danger, row=1, custom_id="red_random")
    async def red_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        赤い「ランダム」ボタンが押された時の処理。
        「<:b431:1289782471197458495> を付けた投稿は除外」かつ
        「自分の投稿」「特定の投稿者」「直前と同じ投稿者」を除外してランダムに選ぶ。
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

    # --------------------------------------------
    # ボタン：あとで読む（赤）
    # --------------------------------------------
    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.danger, row=1, custom_id="conditional_read_later")
    async def conditional_read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        赤い「あとで読む」ボタンが押された時の処理。
        「<:b434:1304690617405669376> を付けた投稿」かつ
        「<:b431:1289782471197458495> を付けた投稿は除外」かつ
        「自分の投稿」「特定の投稿者」「直前と同じ投稿者」を除外してランダムに選ぶ。
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
# パネルメッセージを送信する関数
# ------------------------------------------------
current_panel_message_id = None

async def send_panel(channel):
    """
    ボタンをまとめたパネルを指定したチャンネルに送信し、
    既存のパネルがあれば削除する。
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
    パネルの機能説明を記載したEmbedを作成して返す。
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
# パネルを表示するためのコマンド
# ------------------------------------------------
@bot.tree.command(name="panel", description="パネルを表示します。")
async def panel(interaction: discord.Interaction):
    """
    /panel コマンド：コントロールパネルを表示する。
    """
    channel = interaction.channel
    if channel:
        await interaction.response.send_message("パネルを表示します！", ephemeral=True)
        await send_panel(channel)
    else:
        await interaction.response.send_message("エラー: チャンネルが取得できませんでした。", ephemeral=True)

# ------------------------------------------------
# スラッシュコマンド：/add_data
# 任意のテキストをmessagesテーブルに新規登録するサンプルコマンド
# ------------------------------------------------
@bot.tree.command(name="add_data", description="指定した文字列をデータベース(messagesテーブル)に追加します。")
async def add_data(interaction: discord.Interaction, content: str):
    """
    /add_data コマンド：ユーザーから渡されたテキストをmessagesテーブルにINSERTする。
    """
    # ランダムでメッセージIDを生成（被らないように工夫。ここでは簡易的に実装）
    message_id = random.randint(10**7, 10**8 - 1)  # 7~8桁のランダム数字

    conn = get_db_connection()
    if not conn:
        await interaction.response.send_message("データベースに接続できませんでした。", ephemeral=True)
        return

    try:
        with conn.cursor() as cur:
            # author_idは実際にはサーバーユーザーに合わせて調整してください
            # 今回は実行者のIDをそのまま格納
            author_id = interaction.user.id

            # スレッドIDは固定のTHREAD_IDを使用
            thread_id = THREAD_ID

            # 反応は空のJSONとする
            reactions_json = json.dumps({})

            # INSERTするSQL
            # ※ オン重複時は更新しないようにするには「ON CONFLICT DO NOTHING」などを利用します
            cur.execute(
                """
                INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (message_id, thread_id, author_id, reactions_json, content)
            )
            conn.commit()
        
        await interaction.response.send_message(
            f"データを追加しました。\n"
            f"**message_id**: {message_id}\n"
            f"**content**: {content}",
            ephemeral=True
        )
    except Error as e:
        logging.error(f"Error adding data to DB: {e}")
        await interaction.response.send_message("データを追加中にエラーが発生しました。", ephemeral=True)
    finally:
        release_db_connection(conn)

# ------------------------------------------------
# リアクションイベント：追加
# ------------------------------------------------
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """
    リアクションが追加された時のイベント。
    カスタム絵文字のみDBを更新する。
    """
    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        return
    message = await safe_fetch_message(channel, payload.message_id)
    if message is None:
        return
    if payload.emoji.is_custom_emoji():
        await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=True)

# ------------------------------------------------
# リアクションイベント：削除
# ------------------------------------------------
@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    """
    リアクションが削除された時のイベント。
    カスタム絵文字のみDBを更新する。
    """
    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        return
    message = await safe_fetch_message(channel, payload.message_id)
    if message is None:
        return
    if payload.emoji.is_custom_emoji():
        await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=False)

# ------------------------------------------------
# Botが起動したとき
# ------------------------------------------------
@bot.event
async def on_ready():
    """
    Botが準備完了したときに呼ばれるイベント。
    スラッシュコマンドの同期を行う。
    """
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
    """
    60分ごとに指定スレッドのメッセージをデータベースに保存するタスク。
    """
    await save_all_messages_to_db()

async def save_all_messages_to_db():
    """
    THREAD_IDで指定されたチャンネル（スレッド）からメッセージを取得し、DBに保存する。
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
    取得した複数のメッセージをまとめてデータベースに登録または更新する。
    """
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
