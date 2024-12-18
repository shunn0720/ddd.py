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

class DatabaseQueryError(Exception):
    pass

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

DATABASE_URL = os.getenv("DATABASE_URL")

try:
    db_pool = pool.SimpleConnectionPool(
        minconn=1, maxconn=10, dsn=DATABASE_URL, sslmode='require'
    )
    logging.info("データベース接続プールが初期化されました。")
except Error as e:
    logging.error(
        f"データベース接続プール初期化エラー: {e} "
        f"pgcode={getattr(e, 'pgcode', '')}, "
        f"detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}"
    )
    db_pool = None

def get_db_connection():
    """プールからDBコネクションを取得する"""
    try:
        if db_pool:
            return db_pool.getconn()
        else:
            raise Error("データベース接続プールが初期化されていません。")
    except Error as e:
        logging.error(
            f"データベース接続中にエラー: {e} "
            f"pgcode={getattr(e, 'pgcode', '')}, "
            f"detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}"
        )
        return None

def release_db_connection(conn):
    """DBコネクションをプールに返す"""
    try:
        if db_pool and conn:
            db_pool.putconn(conn)
    except Error as e:
        logging.error(
            f"データベース接続のリリース中にエラー: {e} "
            f"pgcode={getattr(e, 'pgcode', '')}, "
            f"detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}"
        )

def initialize_db():
    """テーブル作成などの初期化処理
    messagesテーブル、reactionsテーブルを作成
    """
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            # メッセージ本体を格納するテーブル
            cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                message_id BIGINT NOT NULL UNIQUE,
                thread_id BIGINT NOT NULL,
                author_id BIGINT NOT NULL,
                content TEXT
            )
            """)

            # リアクション情報を格納するテーブル（messagesと外部キーで連動）
            cur.execute("""
            CREATE TABLE IF NOT EXISTS reactions (
                id SERIAL PRIMARY KEY,
                message_id BIGINT NOT NULL,
                emoji_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                FOREIGN KEY (message_id) REFERENCES messages(message_id) ON DELETE CASCADE
            )
            """)
            conn.commit()
        logging.info("データベースの初期化が完了しました。")
    except Error as e:
        logging.error(
            f"テーブル初期化中エラー: {e} "
            f"pgcode={getattr(e, 'pgcode', '')}, "
            f"detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}"
        )
    finally:
        release_db_connection(conn)

initialize_db()

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

THREAD_ID = 1288407362318893109  # 対象スレッドIDを設定
READ_LATER_REACTION_ID = 1304690617405669376
FAVORITE_REACTION_ID = 1304690627723657267
RANDOM_EXCLUDE_REACTION_ID = 1289782471197458495
SPECIAL_EXCLUDE_AUTHOR = 695096014482440244

last_chosen_authors = {}
current_panel_message_id = None

async def run_in_threadpool(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func, *args, **kwargs)

def save_message_sync(message_id, author_id, content):
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO messages (message_id, thread_id, author_id, content)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (message_id) DO UPDATE SET content = EXCLUDED.content
            """, (
                message_id,
                THREAD_ID,
                author_id,
                content
            ))
            conn.commit()
    except Error as e:
        logging.error(
            f"メッセージ保存中エラー: {e} "
            f"pgcode={getattr(e, 'pgcode', '')}, "
            f"detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}"
        )
    finally:
        release_db_connection(conn)

async def save_message_to_db(message):
    """1件のメッセージをDBに保存"""
    await run_in_threadpool(save_message_sync, message.id, message.author.id, message.content)

def bulk_save_messages_sync(messages):
    conn = get_db_connection()
    if not conn or not messages:
        return
    try:
        data = []
        for message in messages:
            data.append((message.id, THREAD_ID, message.author.id, message.content))

        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO messages (message_id, thread_id, author_id, content)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (message_id) DO UPDATE SET content = EXCLUDED.content
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

async def bulk_save_messages_to_db(messages):
    await run_in_threadpool(bulk_save_messages_sync, messages)

def add_reaction_sync(message_id: int, emoji_id: int, user_id: int):
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO reactions (message_id, emoji_id, user_id)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (message_id, emoji_id, user_id))
            conn.commit()
    except Error as e:
        logging.error(
            f"リアクション追加中エラー: {e} "
            f"pgcode={getattr(e, 'pgcode', '')}, "
            f"detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}"
        )
    finally:
        release_db_connection(conn)

async def add_reaction_to_db(message_id: int, emoji_id: int, user_id: int):
    await run_in_threadpool(add_reaction_sync, message_id, emoji_id, user_id)

def remove_reaction_sync(message_id: int, emoji_id: int, user_id: int):
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM reactions
                WHERE message_id = %s AND emoji_id = %s AND user_id = %s
            """, (message_id, emoji_id, user_id))
            conn.commit()
    except Error as e:
        logging.error(
            f"リアクション削除中エラー: {e} "
            f"pgcode={getattr(e, 'pgcode', '')}, "
            f"detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}"
        )
    finally:
        release_db_connection(conn)

async def remove_reaction_from_db(message_id: int, emoji_id: int, user_id: int):
    await run_in_threadpool(remove_reaction_sync, message_id, emoji_id, user_id)

def user_reacted(msg, reaction_id, user_id):
    """ユーザーが特定の絵文字で反応しているかをDBから確認する関数を実装可能。
    ここではDB参照が必要、簡易的なサンプルとして下記処理を行う。
    """
    # 反応情報はreactionsテーブルにあるため、ここで同期的に取得して確認する
    conn = get_db_connection()
    if not conn:
        return False
    reacted = False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM reactions
                WHERE message_id = %s AND emoji_id = %s AND user_id = %s
                LIMIT 1
            """, (msg['message_id'], reaction_id, user_id))
            reacted = (cur.fetchone() is not None)
    except Error as e:
        logging.error(
            f"リアクション確認中エラー: {e} "
            f"pgcode={getattr(e, 'pgcode', '')}, "
            f"detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}"
        )
    finally:
        release_db_connection(conn)
    return reacted

def get_random_message_sync(thread_id, filter_func=None):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT message_id, thread_id, author_id, content FROM messages WHERE thread_id = %s", (thread_id,))
            messages = cur.fetchall()

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

async def get_random_message(thread_id, filter_func=None):
    return await run_in_threadpool(get_random_message_sync, thread_id, filter_func)

async def send_panel(channel):
    global current_panel_message_id
    if current_panel_message_id:
        try:
            panel_message = await channel.fetch_message(current_panel_message_id)
            await panel_message.delete()
            logging.info(f"以前のパネルメッセージ {current_panel_message_id} を削除しました。")
        except discord.NotFound:
            logging.info(f"以前のパネルメッセージ {current_panel_message_id} は既に削除済みか存在しません。")
        except discord.HTTPException as e:
            logging.error(f"パネルメッセージ削除中エラー: {e}")

    embed = create_panel_embed()
    view = CombinedView()
    try:
        sent_message = await channel.send(embed=embed, view=view)
        current_panel_message_id = sent_message.id
        logging.info(f"新しいパネルメッセージ {current_panel_message_id} を送信しました。")
    except discord.HTTPException as e:
        logging.error(f"パネルメッセージ送信中エラー: {e}")

def is_specific_user():
    """特定ユーザーのみコマンドを実行可能にするためのチェック"""
    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.id == 822460191118721034
    return app_commands.check(predicate)

def create_panel_embed():
    embed = discord.Embed(
        description=(
            "🎯ｴﾛ漫画ﾙｰﾚｯﾄ\n\n"
            "botがｴﾛ漫画を選んでくれるよ！\n\n"
            "🔵：自分の<:b431:1289782471197458495>を除外しない\n"
            "🔴：自分の<:b431:1289782471197458495>を除外する\n\n"
            "【ランダム】：全体から選ぶ\n"
            "【あとで読む】：<:b434:1304690617405669376>を付けた投稿から選ぶ\n"
            "【お気に入り】：<:b435:1304690627723657267>を付けた投稿から選ぶ"
        ),
        color=discord.Color.magenta()
    )
    return embed

class CombinedView(discord.ui.View):
    """パネル上のボタンを扱うViewクラス"""

    def __init__(self):
        super().__init__(timeout=None)

    async def get_author_name(self, author_id):
        user = bot.get_user(author_id)
        if user is None:
            try:
                user = await bot.fetch_user(author_id)
            except discord.NotFound:
                user = None
        return user.display_name if user and user.display_name else (user.name if user else "不明なユーザー")

    async def handle_selection(self, interaction, random_message):
        try:
            if random_message:
                last_chosen_authors[interaction.user.id] = random_message['author_id']
                author_name = await self.get_author_name(random_message['author_id'])
                await interaction.channel.send(
                    f"{interaction.user.mention} さんには、{author_name} さんが投稿したこの本がおすすめだよ！\n"
                    f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
                )
            else:
                await interaction.channel.send(
                    f"{interaction.user.mention} 条件に合う投稿なかったから、また試してみて！。"
                )
        except Exception as e:
            logging.error(f"メッセージ取得/応答中エラー: {e}")
            await interaction.channel.send(
                f"{interaction.user.mention} エラーが発生たから、また後で試して！"
            )
        finally:
            # パネルを再送信して最下部に移動させる
            await send_panel(interaction.channel)

    async def get_and_handle_random_message(self, interaction, filter_func):
        try:
            random_message = await get_random_message(THREAD_ID, filter_func)
            await self.handle_selection(interaction, random_message)
        except Exception as e:
            logging.error(f"ボタン押下時エラー: {e}")
            await interaction.channel.send(f"{interaction.user.mention} 処理中にエラーが発生しました。再試行してください。")

    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary, row=0)
    async def random_normal(self, interaction: discord.Interaction, button: discord.ui.Button):
        def filter_func(msg):
            if msg['author_id'] == interaction.user.id:
                return False
            if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True
        await self.get_and_handle_random_message(interaction, filter_func)

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary, row=0)
    async def read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        def filter_func(msg):
            if not user_reacted(msg, READ_LATER_REACTION_ID, interaction.user.id):
                return False
            if msg['author_id'] == interaction.user.id or msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True
        await self.get_and_handle_random_message(interaction, filter_func)

    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary, row=0)
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        def filter_func(msg):
            if not user_reacted(msg, FAVORITE_REACTION_ID, interaction.user.id):
                return False
            if msg['author_id'] == interaction.user.id or msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True
        await self.get_and_handle_random_message(interaction, filter_func)

    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.danger, row=1)
    async def random_exclude(self, interaction: discord.Interaction, button: discord.ui.Button):
        def filter_func(msg):
            if user_reacted(msg, RANDOM_EXCLUDE_REACTION_ID, interaction.user.id):
                return False
            if msg['author_id'] == interaction.user.id or msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True
        await self.get_and_handle_random_message(interaction, filter_func)

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.danger, row=1)
    async def conditional_read(self, interaction: discord.Interaction, button: discord.ui.Button):
        def filter_func(msg):
            if not user_reacted(msg, READ_LATER_REACTION_ID, interaction.user.id):
                return False
            if user_reacted(msg, RANDOM_EXCLUDE_REACTION_ID, interaction.user.id):
                return False
            if msg['author_id'] == interaction.user.id or msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True
        await self.get_and_handle_random_message(interaction, filter_func)


@bot.tree.command(name="panel")
@is_specific_user()
async def panel(interaction: discord.Interaction):
    await interaction.response.defer()
    channel = interaction.channel
    if channel is None:
        logging.warning("コマンドを実行したチャンネルが取得できません。")
        await interaction.followup.send("エラーが発生しました。チャンネルが特定できません。もう一度お試しください。", ephemeral=True)
        return
    await send_panel(channel)

@bot.tree.command(name="update_db")
@is_specific_user()
async def update_db(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    try:
        await save_all_messages_to_db()
        await interaction.followup.send("全てのメッセージをデータベースに保存しました。", ephemeral=True)
    except Exception as e:
        logging.error(f"update_dbコマンド中エラー: {e}")
        await interaction.followup.send("エラーが発生しました。再試行してください。", ephemeral=True)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
    else:
        logging.error(f"Unhandled app command error: {error}")
        await interaction.response.send_message("コマンド実行中にエラーが発生しました。もう一度お試しください。", ephemeral=True)

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.emoji.is_custom_emoji():
        await add_reaction_to_db(payload.message_id, payload.emoji.id, payload.user_id)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if payload.emoji.is_custom_emoji():
        await remove_reaction_from_db(payload.message_id, payload.emoji.id, payload.user_id)

async def save_all_messages_to_db():
    """チャンネル内の全メッセージをページングしてすべてDBに保存する"""
    channel = bot.get_channel(THREAD_ID)
    if channel is None:
        logging.error("指定されたTHREAD_IDのチャンネルが見つかりません。")
        return

    batch_size = 100
    total_saved = 0
    last_message = None

    while True:
        # beforeを指定してページング
        if last_message:
            history = channel.history(limit=batch_size, before=last_message)
        else:
            history = channel.history(limit=batch_size)
        
        messages = [m async for m in history]
        if not messages:
            # もう取得できるメッセージがない
            break

        await bulk_save_messages_to_db(messages)
        total_saved += len(messages)
        last_message = messages[-1].created_at
        logging.info(f"累計{total_saved}件のメッセージをデータベースに保存しました。")

    logging.info("全てのメッセージをデータベースに保存しました。")

@tasks.loop(hours=24)
async def cleanup_deleted_messages_task():
    """(オプション) 定期的にDB内メッセージがDiscord上にまだ存在するか確認し、ないものを削除する処理なども可能"""
    # 実装例は必要に応じて
    pass

@bot.event
async def on_ready():
    cleanup_deleted_messages_task.start()
    logging.info(f"Botが起動しました！ {bot.user}")
    try:
        synced = await bot.tree.sync()
        logging.info(f"スラッシュコマンドが同期されました: {synced}")
    except Exception as e:
        logging.error(f"スラッシュコマンド同期中エラー: {e}")

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
