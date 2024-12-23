import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
import asyncio
import logging
import psycopg2
from psycopg2 import pool, Error
from psycopg2.extras import DictCursor
from dotenv import load_dotenv
import json

# =======================
# カスタム例外
# =======================
class DatabaseQueryError(Exception):
    pass

# =======================
# 環境変数の読み込み
# =======================
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
PANEL_CONFIG_FILE = os.getenv("PANEL_CONFIG_FILE", "panel_config.json")

# リアクションIDの取得
try:
    READ_LATER_REACTION_ID = int(os.getenv("READ_LATER_REACTION_ID"))
    FAVORITE_REACTION_ID = int(os.getenv("FAVORITE_REACTION_ID"))
    RANDOM_EXCLUDE_REACTION_ID = int(os.getenv("RANDOM_EXCLUDE_REACTION_ID"))
    SPECIAL_EXCLUDE_AUTHOR = int(os.getenv("SPECIAL_EXCLUDE_AUTHOR"))
except (TypeError, ValueError) as e:
    logging.critical(f".envファイルのリアクションIDまたは特定投稿者IDが無効です: {e}")
    raise SystemExit("リアクションIDまたは特定投稿者IDの設定が無効です。Botを終了します。")

# =======================
# ログ設定
# =======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

# =======================
# データベース接続プールの初期化
# =======================
db_pool = None
try:
    db_pool = pool.SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        dsn=DATABASE_URL,
        sslmode='require'  # 必要に応じて 'require' 以外に変更
    )
    logging.info("データベース接続プールが初期化されました。")
except (Error, Exception) as e:
    logging.error(f"データベース接続プール初期化エラー: {e}")
    db_pool = None

def get_db_connection():
    """
    プールからコネクションを取得
    """
    if not db_pool:
        logging.error("データベース接続プールが初期化されていません。")
        return None
    try:
        return db_pool.getconn()
    except Error as e:
        logging.error(f"データベース接続取得中エラー: {e}")
        return None

def release_db_connection(conn):
    """
    使用後のコネクションをプールに返す
    """
    if db_pool and conn:
        try:
            db_pool.putconn(conn)
        except Error as e:
            logging.error(f"データベース接続のリリース中にエラー: {e}")

# =======================
# DBテーブル初期化
# =======================
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
        logging.info("データベースの初期化が完了しました。")
    except Error as e:
        logging.error(f"テーブル初期化中エラー: {e}")
    finally:
        release_db_connection(conn)

initialize_db()

# =======================
# Bot準備
# =======================
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

# =======================
# 各種IDの設定
# =======================
THREAD_ID = 1288407362318893109  # 実際のスレッドIDに置き換えてください

# 連続して同じ投稿者を選ばないよう管理
last_chosen_authors = {}

# 現在表示中のパネルメッセージID
current_panel_message_id = None

# =======================
# パネルメッセージIDの保存・読み込み関数
# =======================
def save_panel_message_id(message_id: int):
    """
    パネルメッセージIDをJSONファイルに保存
    """
    data = {"panel_message_id": message_id}
    try:
        with open(PANEL_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logging.info(f"パネルメッセージID {message_id} を保存しました。")
    except Exception as e:
        logging.error(f"パネルメッセージIDの保存中にエラー: {e}")

def load_panel_message_id() -> int | None:
    """
    保存したパネルメッセージIDをJSONファイルから読み込む
    """
    try:
        with open(PANEL_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            panel_id = data.get("panel_message_id")
            logging.info(f"パネルメッセージID {panel_id} を読み込みました。")
            return panel_id
    except FileNotFoundError:
        logging.warning("パネルメッセージIDファイルが存在しません。")
        return None
    except Exception as e:
        logging.error(f"パネルメッセージIDの読み込み中にエラー: {e}")
        return None

# =======================
# ヘルパー関数
# =======================
async def run_in_threadpool(func, *args, **kwargs):
    """
    同期関数をスレッドプールで実行して非同期で返す
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func, *args, **kwargs)

def save_message_to_db_sync(message_id, author_id, content):
    """
    単一メッセージをDBに保存（同期）
    """
    conn = get_db_connection()
    if not conn:
        return
    try:
        reactions_json = json.dumps({})
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (message_id) DO UPDATE
                SET thread_id = EXCLUDED.thread_id,
                    author_id = EXCLUDED.author_id,
                    reactions = EXCLUDED.reactions,
                    content = EXCLUDED.content
            """, (
                message_id,
                THREAD_ID,
                author_id,
                reactions_json,
                content
            ))
            conn.commit()
        logging.info(f"メッセージ {message_id} をデータベースに保存しました。")
    except Error as e:
        logging.error(
            f"メッセージ保存中エラー: {e} "
            f"pgcode={getattr(e, 'pgcode', '')}, "
            f"detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}"
        )
    finally:
        release_db_connection(conn)

async def save_message_to_db(message: discord.Message):
    """
    単一メッセージをDBに保存（非同期ラッパー）
    """
    await run_in_threadpool(save_message_to_db_sync, message.id, message.author.id, message.content)

def bulk_save_messages_to_db_sync(messages):
    """
    複数メッセージをまとめてDBに保存
    """
    conn = get_db_connection()
    if not conn or not messages:
        return
    try:
        data = []
        for msg in messages:
            reactions_json = json.dumps({})
            data.append((msg.id, THREAD_ID, msg.author.id, reactions_json, msg.content))

        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (message_id) DO UPDATE
                SET thread_id = EXCLUDED.thread_id,
                    author_id = EXCLUDED.author_id,
                    reactions = EXCLUDED.reactions,
                    content = EXCLUDED.content
            """, data)
            conn.commit()
        logging.info(f"{len(messages)}件のメッセージをバルク挿入または更新しました。")
    except Error as e:
        logging.error(
            f"バルク挿入中エラー: {e} "
            f"pgcode={getattr(e, 'pgcode', '')}, "
            f"detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}"
        )
    finally:
        release_db_connection(conn)

async def bulk_save_messages_to_db(messages: list[discord.Message]):
    """
    複数メッセージをまとめてDBに保存（非同期ラッパー）
    """
    await run_in_threadpool(bulk_save_messages_to_db_sync, messages)

def update_reactions_in_db_sync(message_id, emoji_id, user_id, add=True):
    """
    メッセージのreactionsフィールドを更新（同期）
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
                reactions = json.loads(reactions)

            str_emoji_id = str(emoji_id)
            user_list = reactions.get(str_emoji_id, [])

            if add:
                if user_id not in user_list:
                    user_list.append(user_id)
            else:
                if user_id in user_list:
                    user_list.remove(user_id)

            reactions[str_emoji_id] = user_list

            cur.execute(
                "UPDATE messages SET reactions = %s WHERE message_id = %s",
                (json.dumps(reactions), message_id)
            )
            conn.commit()
        logging.info(
            f"メッセージ {message_id} のリアクション {emoji_id} を{'追加' if add else '削除'}しました。"
        )
    except Error as e:
        logging.error(
            f"reactions更新中エラー: {e} "
            f"pgcode={getattr(e, 'pgcode', '')}, "
            f"detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}"
        )
    finally:
        release_db_connection(conn)

async def update_reactions_in_db(message_id, emoji_id, user_id, add=True):
    """
    メッセージのreactionsフィールドを更新（非同期ラッパー）
    """
    await run_in_threadpool(update_reactions_in_db_sync, message_id, emoji_id, user_id, add)

def user_reacted(msg_row: dict, reaction_id: int, user_id: int) -> bool:
    """
    DBから取得したmessagesテーブルのrow (dict) に対して、
    指定ユーザーが指定リアクションを押しているかチェック
    """
    reaction_data = msg_row['reactions']
    if reaction_data is None:
        reaction_data = {}
    elif isinstance(reaction_data, str):
        reaction_data = json.loads(reaction_data)
    users = reaction_data.get(str(reaction_id), [])
    return user_id in users

def get_random_message_sync(thread_id: int, filter_func=None) -> dict | None:
    """
    スレッド内のmessagesからランダムに1つを返す
    filter_funcで条件を絞る
    """
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
                    m['reactions'] = json.loads(m['reactions']) or {}

            if filter_func:
                messages = [m for m in messages if filter_func(m)]
            if not messages:
                return None
            return random.choice(messages)
    except Error as e:
        logging.error(
            f"ランダムメッセージ取得中エラー: {e} "
            f"pgcode={getattr(e, 'pgcode', '')}, "
            f"detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}"
        )
        return None
    finally:
        release_db_connection(conn)

async def get_random_message(thread_id: int, filter_func=None) -> dict | None:
    return await run_in_threadpool(get_random_message_sync, thread_id, filter_func)

async def safe_fetch_message(channel: discord.TextChannel, message_id: int) -> discord.Message | None:
    """
    Discordから実際のメッセージを取得
    """
    try:
        message = await channel.fetch_message(message_id)
        return message
    except discord.NotFound:
        logging.warning(f"メッセージ {message_id} は存在しません。")
        return None
    except discord.Forbidden:
        logging.error(f"メッセージ {message_id} へのアクセスが拒否されました。")
        return None
    except discord.HTTPException as e:
        logging.error(f"メッセージ {message_id} の取得中にHTTPエラー: {e}")
        return None

# =======================
# パネルメッセージの送信/更新
# =======================
async def send_panel(channel: discord.TextChannel):
    """
    パネルメッセージを送信または更新する。
    既存のパネルがある場合は削除し、新しいパネルを作成。
    """
    global current_panel_message_id

    # 既にパネルがあれば削除
    if current_panel_message_id:
        try:
            old_panel = await channel.fetch_message(current_panel_message_id)
            await old_panel.delete()
            logging.info(f"以前のパネルメッセージ {current_panel_message_id} を削除しました。")
        except discord.NotFound:
            logging.warning(f"以前のパネルメッセージ {current_panel_message_id} が見つかりません。")
        except discord.HTTPException as e:
            logging.error(f"パネルメッセージ削除中エラー: {e}")

    embed = create_panel_embed()
    view = CombinedView()

    try:
        new_panel = await channel.send(embed=embed, view=view)
        current_panel_message_id = new_panel.id

        # パネルメッセージIDを保存
        save_panel_message_id(current_panel_message_id)

        # Viewを特定のメッセージに紐付けて再登録
        bot.add_view(view, message_id=current_panel_message_id)

        logging.info(f"新しいパネルメッセージ {current_panel_message_id} を送信しました。")
    except discord.HTTPException as e:
        logging.error(f"パネルメッセージ送信中エラー: {e}")

def create_panel_embed() -> discord.Embed:
    """
    パネルの埋め込みを作成
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

# =======================
# View (ボタン) クラス
# =======================
class CombinedView(discord.ui.View):
    """
    各ボタンが押された際の処理とフィルタリングを行うView
    Persistent Viewのためcustom_idを必ず設定
    """
    def __init__(self):
        super().__init__(timeout=None)

    async def get_author_name(self, author_id: int) -> str:
        """
        指定ユーザーIDのユーザー名を取得
        """
        user = bot.get_user(author_id)
        if user is None:
            try:
                user = await bot.fetch_user(author_id)
            except discord.NotFound:
                user = None
        if user and user.display_name:
            return user.display_name
        elif user:
            return user.name
        else:
            return "不明なユーザー"

    async def handle_selection(self, interaction: discord.Interaction, random_message: dict | None):
        """
        選択されたメッセージをユーザーに案内し、パネルを再送信する
        """
        try:
            if random_message:
                # 選ばれた作者を記録
                last_chosen_authors[interaction.user.id] = random_message['author_id']
                author_name = await self.get_author_name(random_message['author_id'])
                # メッセージURLを作成
                link = (
                    f"https://discord.com/channels/{interaction.guild_id}/{THREAD_ID}/"
                    f"{random_message['message_id']}"
                )
                await interaction.channel.send(
                    f"{interaction.user.mention} さんには、{author_name} さんが投稿したこの本がおすすめだよ！\n{link}"
                )
            else:
                await interaction.channel.send(
                    f"{interaction.user.mention} 条件に合う投稿が見つかりませんでした。リアクションや条件を確認してください。"
                )
        except Exception as e:
            logging.error(f"メッセージ取得/応答中エラー: {e}")
            await interaction.channel.send(
                f"{interaction.user.mention} エラーが発生しました。もう一度試してみてください。"
            )
        finally:
            # パネルを再送信
            await send_panel(interaction.channel)

    async def get_and_handle_random_message(self, interaction: discord.Interaction, filter_func):
        """
        ランダムメッセージを取得し、handle_selectionに渡す
        """
        try:
            await interaction.response.defer()
            random_message = await get_random_message(THREAD_ID, filter_func)
            await self.handle_selection(interaction, random_message)
        except Exception as e:
            logging.error(f"ボタン押下時エラー: {e}")
            await interaction.channel.send(
                f"{interaction.user.mention} 処理中にエラーが発生しました。もう一度試してください。"
            )

    # ============= 青ボタン =============
    @discord.ui.button(
        label="ランダム",
        style=discord.ButtonStyle.primary,
        row=0,
        custom_id="random_normal_button"
    )
    async def random_normal(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        青ボタン「ランダム」: 全体の投稿からランダムに選択
        除外: 自分の投稿、特定投稿者、Bot、自分が前回選んだ投稿者
        """
        bot_id = bot.user.id

        def filter_func(msg):
            if msg['author_id'] == interaction.user.id:
                return False
            if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                return False
            if msg['author_id'] == bot_id:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func)

    @discord.ui.button(
        label="あとで読む",
        style=discord.ButtonStyle.primary,
        row=0,
        custom_id="read_later_button"
    )
    async def read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        青ボタン「あとで読む」: ユーザーが <:b434:...> リアクションを付けた投稿からランダムに選択
        除外: 自分の投稿、特定投稿者、Bot、自分が前回選んだ投稿者
        """
        bot_id = bot.user.id

        def filter_func(msg):
            if not user_reacted(msg, READ_LATER_REACTION_ID, interaction.user.id):
                return False
            if msg['author_id'] == interaction.user.id:
                return False
            if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                return False
            if msg['author_id'] == bot_id:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func)

    @discord.ui.button(
        label="お気に入り",
        style=discord.ButtonStyle.primary,
        row=0,
        custom_id="favorite_button"
    )
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        青ボタン「お気に入り」: ユーザーが <:b435:...> リアクションを付けた投稿からランダムに選択
        除外: 自分の投稿、特定投稿者、Bot、自分が前回選んだ投稿者
        """
        bot_id = bot.user.id

        def filter_func(msg):
            if not user_reacted(msg, FAVORITE_REACTION_ID, interaction.user.id):
                return False
            if msg['author_id'] == interaction.user.id:
                return False
            if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                return False
            if msg['author_id'] == bot_id:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func)

    # ============= 赤ボタン =============
    @discord.ui.button(
        label="ランダム",
        style=discord.ButtonStyle.danger,
        row=1,
        custom_id="random_exclude_button"
    )
    async def random_exclude(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        赤ボタン「ランダム」: ユーザーが <:b431:...> リアクションを付けた投稿を除外してランダムに選択
        除外: 自分の投稿、特定投稿者、Bot、自分が前回選んだ投稿者
        """
        bot_id = bot.user.id

        def filter_func(msg):
            if user_reacted(msg, RANDOM_EXCLUDE_REACTION_ID, interaction.user.id):
                return False
            if msg['author_id'] == interaction.user.id:
                return False
            if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                return False
            if msg['author_id'] == bot_id:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func)

    @discord.ui.button(
        label="あとで読む",
        style=discord.ButtonStyle.danger,
        row=1,
        custom_id="conditional_read_button"
    )
    async def conditional_read(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        赤ボタン「あとで読む」: ユーザーが <:b434:...> リアクションを付けた投稿のうち、
        <:b431:...> リアクションを付けたものを除外してランダムに選択
        除外: 自分の投稿、特定投稿者、Bot、自分が前回選んだ投稿者
        """
        bot_id = bot.user.id

        def filter_func(msg):
            if not user_reacted(msg, READ_LATER_REACTION_ID, interaction.user.id):
                return False
            if user_reacted(msg, RANDOM_EXCLUDE_REACTION_ID, interaction.user.id):
                return False
            if msg['author_id'] == interaction.user.id:
                return False
            if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                return False
            if msg['author_id'] == bot_id:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func)

# =======================
# スラッシュコマンドの定義
# =======================
def is_specific_user():
    """
    特定のユーザーのみコマンドを実行できるようにするチェック
    """
    async def predicate(interaction: discord.Interaction) -> bool:
        # 例: ユーザーIDが 822460191118721034 のユーザーのみ実行可能
        return interaction.user.id == 822460191118721034
    return app_commands.check(predicate)

@bot.tree.command(name="panel")
@is_specific_user()
async def panel(interaction: discord.Interaction):
    """
    特定ユーザーのみ実行可能。パネルを表示。
    """
    channel = interaction.channel
    if channel is None:
        logging.error("コマンドを実行したチャンネルが取得できません。")
        await interaction.response.send_message("エラー: チャンネルが特定できませんでした。", ephemeral=False)
        return

    # パネルを送信
    await interaction.response.defer()
    await interaction.channel.send("パネルを表示します！")
    await send_panel(channel)

@bot.tree.command(name="update_db")
@is_specific_user()
async def update_db(interaction: discord.Interaction):
    """
    特定ユーザーのみ実行可能。指定スレッドのメッセージをDBに更新
    """
    await interaction.response.defer()
    await interaction.channel.send("データベースを更新しています...")
    try:
        await save_all_messages_to_db()
        await interaction.channel.send("全てのメッセージをデータベースに保存しました。")
    except Exception as e:
        logging.error(f"update_dbコマンド中エラー: {e}")
        await interaction.channel.send("エラーが発生しました。しばらくしてから再試行してください。")

# スラッシュコマンドエラー時の共通処理
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.channel.send("このコマンドを実行する権限がありません。")
    else:
        logging.error(f"Unhandled app command error: {error}")
        await interaction.channel.send("コマンド実行中にエラーが発生しました。もう一度お試しください。")

# =======================
# リアクションイベント
# =======================
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """
    リアクションが追加されたらDBを更新
    """
    if payload.emoji.is_custom_emoji():
        await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=True)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    """
    リアクションが削除されたらDBを更新
    """
    if payload.emoji.is_custom_emoji():
        await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=False)

# =======================
# 定期タスク: メッセージをDBに保存
# =======================
@tasks.loop(minutes=60)
async def save_all_messages_to_db_task():
    await save_all_messages_to_db()

async def save_all_messages_to_db():
    """
    THREAD_ID の最新メッセージを取得してDBへ保存する
    """
    channel = bot.get_channel(THREAD_ID)
    if not channel:
        logging.error("指定されたTHREAD_IDのチャンネルが見つかりません。")
        return

    try:
        limit_count = 100
        messages = []
        async for message in channel.history(limit=limit_count):
            messages.append(message)
        if messages:
            await bulk_save_messages_to_db(messages)
            logging.info(f"最大{limit_count}件のメッセージをデータベースに保存しました。")
        else:
            logging.info("取得できるメッセージがありませんでした。")
    except discord.HTTPException as e:
        logging.error(f"メッセージ履歴取得中エラー: {e}")

# =======================
# Bot起動時の処理
# =======================
@bot.event
async def on_ready():
    """
    Botが起動したときの処理
    - Persistent View の再登録
    - 定期タスク開始
    - スラッシュコマンド同期
    """
    logging.info(f"Botが起動しました！ {bot.user}")

    # パネルメッセージIDを読み込み、Persistent Viewとして再登録
    stored_panel_message_id = load_panel_message_id()
    if stored_panel_message_id:
        try:
            # 既存のパネルメッセージに対してViewを再登録
            bot.add_view(CombinedView(), message_id=stored_panel_message_id)
            logging.info(f"パネルメッセージID {stored_panel_message_id} に紐付くViewを再登録しました。")
        except Exception as e:
            logging.error(f"View再登録中エラー: {e}")

    # 定期タスク開始
    save_all_messages_to_db_task.start()

    # スラッシュコマンド同期
    try:
        synced = await bot.tree.sync()
        logging.info(f"スラッシュコマンドが同期されました: {synced}")
    except Exception as e:
        logging.error(f"スラッシュコマンド同期中エラー: {e}")

# =======================
# Bot実行
# =======================
if DISCORD_TOKEN:
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logging.error(f"Bot起動中エラー: {e}")
        if db_pool:
            db_pool.closeall()
            logging.info("データベース接続プールをクローズしました。")
else:
    logging.error("DISCORD_TOKENが設定されていません。")
