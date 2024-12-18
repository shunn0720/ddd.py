import os
import json
import asyncio
import logging
import random
import discord
from discord.ext import tasks, commands
from discord import app_commands
import psycopg2
from psycopg2 import pool, Error
from psycopg2.extras import DictCursor
from dotenv import load_dotenv

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
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# 定数類
THREAD_ID = 1288407362318893109
READ_LATER_REACTION_ID = 1304690617405669376
FAVORITE_REACTION_ID = 1304690627723657267
RANDOM_EXCLUDE_REACTION_ID = 1289782471197458495
SPECIAL_EXCLUDE_AUTHOR = 695096014482440244

last_chosen_authors = {}
current_panel_message_id = None

# データベースプール初期化
try:
    db_pool = pool.SimpleConnectionPool(
        minconn=1, maxconn=10, dsn=DATABASE_URL, sslmode='require'
    )
    logging.info("データベース接続プールが初期化されました。")
except Error as e:
    logging.error(f"データベース接続プールの初期化中にエラー: {e}")
    db_pool = None

def get_db_connection():
    """プールからDBコネクションを取得"""
    try:
        if db_pool:
            return db_pool.getconn()
        else:
            raise Error("データベース接続プールが初期化されていません。")
    except Error as e:
        logging.error(f"データベース接続中にエラー: {e}")
        return None

def release_db_connection(conn):
    """DBコネクションをプールに返す"""
    try:
        if db_pool and conn:
            db_pool.putconn(conn)
    except Error as e:
        logging.error(f"データベース接続のリリース中にエラー: {e}")

def initialize_db():
    """テーブル作成などの初期化処理"""
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            # テーブル作成(例)
            # message_idにユニーク制約をつけON CONFLICT対応可能なようにする
            cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                message_id BIGINT PRIMARY KEY,
                thread_id BIGINT,
                author_id BIGINT,
                reactions JSONB,
                content TEXT
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

# Botインスタンス作成(例)
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.message_content = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

async def run_in_threadpool(func, *args, **kwargs):
    """同期的なDB処理をスレッドプールで非同期的に実行"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func, *args, **kwargs)

async def get_reactions_dict(message):
    """メッセージのreactionsを辞書形式で取得（emoji_id文字列キー: ユーザーIDリスト）"""
    reactions = {}
    for reaction in message.reactions:
        if hasattr(reaction.emoji, 'id') and reaction.emoji.id is not None:
            users = [user.id async for user in reaction.users()]
            reactions[str(reaction.emoji.id)] = users
    return reactions

# メッセージを安全に取得する関数
async def safe_fetch_message(channel: discord.TextChannel, message_id: int):
    """メッセージを安全に取得する関数。存在しない場合はNoneを返す。"""
    try:
        message = await channel.fetch_message(message_id)
        return message
    except discord.NotFound:
        logging.warning(f"メッセージ {message_id} は存在しません。スキップします。")
        return None
    except discord.Forbidden:
        logging.error(f"メッセージ {message_id} へのアクセスが拒否されました。")
        return None
    except discord.HTTPException as e:
        logging.error(f"メッセージ {message_id} の取得中にHTTPエラーが発生しました: {e}")
        return None

def save_message_to_db_sync(message_id, thread_id, author_id, reactions_json, content):
    """同期的なメッセージ保存処理（DBアクセス）"""
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            # ON CONFLICTでreactions, contentを更新する
            cur.execute("""
            INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (message_id) DO UPDATE
            SET reactions = EXCLUDED.reactions,
                content = EXCLUDED.content
            """, (
                message_id,
                thread_id,
                author_id,
                reactions_json,
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
    """非同期関数から呼び出せるメッセージ保存用ラッパ"""
    # reactions取得は非同期なのでここで実行
    reactions_dict = await get_reactions_dict(message)
    reactions_json = json.dumps(reactions_dict)
    await run_in_threadpool(save_message_to_db_sync, message.id, THREAD_ID, message.author.id, reactions_json, message.content)

def bulk_save_messages_to_db_sync(messages):
    """同期的なバルク挿入"""
    conn = get_db_connection()
    if not conn or not messages:
        return
    try:
        data = []
        for message in messages:
            # ここではリアクション情報を一旦空辞書とする(後でupdateすることを想定)
            # 必要であればasyncで処理し、run_in_threadpoolでわける
            reactions_json = json.dumps({})
            data.append((message.id, THREAD_ID, message.author.id, reactions_json, message.content))

        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (message_id) DO UPDATE 
                SET reactions = EXCLUDED.reactions,
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

async def bulk_save_messages_to_db(messages):
    """非同期関数。メッセージバルク保存"""
    # 必要ならリアクション取得も行えるが、ここでは簡略化のため省略
    await run_in_threadpool(bulk_save_messages_to_db_sync, messages)

def update_reactions_in_db_sync(message_id, emoji_id, user_id, add=True):
    """同期的なreactions更新処理"""
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT reactions FROM messages WHERE message_id = %s", (message_id,))
            row = cur.fetchone()
            if not row:
                # 未登録のメッセージなら更新不要
                return
            reaction_data = row['reactions'] or {}
            if isinstance(reaction_data, str):
                reaction_data = json.loads(reaction_data)

            str_emoji_id = str(emoji_id)
            user_list = reaction_data.get(str_emoji_id, [])
            if add:
                if user_id not in user_list:
                    user_list.append(user_id)
            else:
                if user_id in user_list:
                    user_list.remove(user_id)
            reaction_data[str_emoji_id] = user_list

            cur.execute("UPDATE messages SET reactions = %s WHERE message_id = %s",
                        (json.dumps(reaction_data), message_id))
            conn.commit()
    except Error as e:
        logging.error(
            f"reactions更新中エラー: {e} "
            f"pgcode={getattr(e, 'pgcode', '')}, "
            f"detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}"
        )
    finally:
        release_db_connection(conn)

async def update_reactions_in_db(message_id, emoji_id=None, user_id=None, add=True):
    """非同期版：メッセージ取得後にreactions更新"""
    channel = bot.get_channel(THREAD_ID)
    if channel is None:
        logging.error(f"チャンネル {THREAD_ID} が見つかりませんでした。スキップします。")
        return

    message = await safe_fetch_message(channel, message_id)
    if message is None:
        # メッセージ取得不可ならスキップ
        return

    # DB更新
    await run_in_threadpool(update_reactions_in_db_sync, message_id, emoji_id, user_id, add)

def user_reacted(msg, reaction_id, user_id):
    """特定ユーザーが特定reaction_idに反応しているか判定"""
    reaction_data = msg['reactions']
    if reaction_data is None:
        reaction_data = {}
    if isinstance(reaction_data, str):
        reaction_data = json.loads(reaction_data)
    users = reaction_data.get(str(reaction_id), [])
    return user_id in users

def get_random_message_sync(thread_id, filter_func=None):
    """同期的なランダムメッセージ取得"""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT message_id, thread_id, author_id, reactions, content FROM messages WHERE thread_id = %s", (thread_id,))
            rows = cur.fetchall()
            messages = []
            for row in rows:
                msg = {
                    'message_id': row['message_id'],
                    'thread_id': row['thread_id'],
                    'author_id': row['author_id'],
                    'reactions': row['reactions'],
                    'content': row['content']
                }
                if filter_func is None or filter_func(msg):
                    messages.append(msg)
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

def create_panel_embed():
    """パネル表示用のEmbed生成"""
    embed = discord.Embed(
        description=(
            "🎯ｴﾛ漫画ﾙｰﾚｯﾄ\n\n"
            "botがｴﾛ漫画を選んでくれるよ！<a:c296:1288305823323263029>\n\n"
            "🔵：自分の<:b431:1289782471197458495>を除外しない\n"
            "🔴：自分の<:b431:1289782471197458495>を除外する\n\n"
            "【ランダム】：全体から選ぶ\n"
            "【積読限定】：自分の <a:c288:1304690617405669376> のみ\n"
            "【お気に入り限定】：自分の <a:c287:1304690627723657267> のみ\n"
            "【自分の除外限定】：自分の <:b431:1289782471197458495> を除外\n"
            "【全部除外】：自分の <a:c288:1304690617405669376> と <a:c287:1304690627723657267> のみからさらに自分と特定作者を除外\n"
        )
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
                return "不明なユーザー"
        return user.display_name if user and user.display_name else (user.name if user else "不明なユーザー")

    async def handle_selection(self, interaction, random_message):
        try:
            if random_message:
                last_chosen_authors[interaction.user.id] = random_message['author_id']
                author_name = await self.get_author_name(random_message['author_id'])
                await interaction.channel.send(
                    f"{interaction.user.mention} 当選: {author_name}\n{random_message['content']}"
                )
            else:
                await interaction.channel.send(
                    f"{interaction.user.mention} 条件に合う投稿が見つかりませんでした。もう一度お試しください。"
                )
        except Exception as e:
            logging.error(f"メッセージ取得/応答中エラー: {e}")
            await interaction.channel.send(
                f"{interaction.user.mention} エラーが発生しました。しばらくしてから再試行してください。"
            )
        finally:
            # パネルを再送して最下部に移動させる
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
            return True
        await self.get_and_handle_random_message(interaction, filter_func)

    @discord.ui.button(label="積読限定", style=discord.ButtonStyle.secondary, row=0)
    async def random_read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        def filter_func(msg):
            if not user_reacted(msg, READ_LATER_REACTION_ID, interaction.user.id):
                return False
            if msg['author_id'] == interaction.user.id or msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True
        await self.get_and_handle_random_message(interaction, filter_func)

    @discord.ui.button(label="お気に入り限定", style=discord.ButtonStyle.secondary, row=0)
    async def random_favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        def filter_func(msg):
            if not user_reacted(msg, FAVORITE_REACTION_ID, interaction.user.id):
                return False
            if msg['author_id'] == interaction.user.id or msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True
        await self.get_and_handle_random_message(interaction, filter_func)

    @discord.ui.button(label="自分の除外限定", style=discord.ButtonStyle.secondary, row=1)
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

    @discord.ui.button(label="全部除外", style=discord.ButtonStyle.danger, row=1)
    async def random_all_exclude(self, interaction: discord.Interaction, button: discord.ui.Button):
        def filter_func(msg):
            # READ_LATER/FAVORITEのみ対象かつself/SPECIAL_EXCLUDE_AUTHOR除外
            if not (user_reacted(msg, READ_LATER_REACTION_ID, interaction.user.id) or user_reacted(msg, FAVORITE_REACTION_ID, interaction.user.id)):
                return False
            if user_reacted(msg, RANDOM_EXCLUDE_REACTION_ID, interaction.user.id):
                return False
            if msg['author_id'] == interaction.user.id or msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True
        await self.get_and_handle_random_message(interaction, filter_func)

async def send_panel(channel):
    """パネルメッセージを送信または再表示する"""
    global current_panel_message_id
    # 既存パネルがあれば削除
    if current_panel_message_id:
        try:
            old_msg = await channel.fetch_message(current_panel_message_id)
            await old_msg.delete()
            logging.info(f"以前のパネルメッセージ {current_panel_message_id} を削除しました。")
        except discord.NotFound:
            logging.warning(f"以前のパネルメッセージ {current_panel_message_id} が見つかりません。")
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
    """特定ユーザーのみコマンド実行可能にするチェック (例)"""
    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.id == 822460191118721034
    return app_commands.check(predicate)

@bot.tree.command(name="panel")
@is_specific_user()
async def panel(interaction: discord.Interaction):
    await interaction.response.defer()
    channel = interaction.channel
    if channel is None:
        await interaction.followup.send("エラーが発生しました。チャンネルが特定できません。", ephemeral=True)
        logging.error("コマンドを実行したチャンネルが取得できません。")
        return
    # 即座にメッセージを返してからパネルを送信
    await interaction.followup.send("パネルを表示します！", ephemeral=False)
    await send_panel(channel)

@bot.tree.command(name="update_db")
@is_specific_user()
async def update_db(interaction: discord.Interaction):
    await interaction.response.send_message("データベースを更新しています...", ephemeral=True)
    await interaction.response.defer(thinking=True)
    try:
        await save_all_messages_to_db()
        await interaction.followup.send("全てのメッセージをデータベースに保存しました。", ephemeral=True)
    except Exception as e:
        logging.error(f"update_dbコマンド中エラー: {e}")
        await interaction.followup.send("エラーが発生しました。しばらくしてから再試行してください。", ephemeral=True)

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
        await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=True)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if payload.emoji.is_custom_emoji():
        await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=False)

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
            logging.info(f"最大{limit_count}件のメッセージをデータベースに保存しました。")
        except discord.HTTPException as e:
            logging.error(f"メッセージ履歴取得中エラー: {e}")
    else:
        logging.error("指定されたTHREAD_IDのチャンネルが見つかりません。")

@bot.event
async def on_disconnect():
    logging.warning("Botが接続を失いました。再接続を待っています。")

@bot.event
async def on_resumed():
    logging.info("BotがDiscordに再接続しました。")

@bot.event
async def on_ready():
    logging.info(f"Botが起動しました！ {bot.user}")
    try:
        synced = await bot.tree.sync()
        logging.info(f"スラッシュコマンドが同期されました: {synced}")
    except Exception as e:
        logging.error(f"スラッシュコマンド同期中エラー: {e}")

# Bot起動前にDB初期化
initialize_db()

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
