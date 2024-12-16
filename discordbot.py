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
except Error as e:
    logging.error(f"データベース接続プールの初期化中にエラー: {e} pgcode={getattr(e, 'pgcode', '')}, detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}")
    db_pool = None

def get_db_connection():
    try:
        if db_pool:
            return db_pool.getconn()
        else:
            raise Error("データベース接続プールが初期化されていません。")
    except Error as e:
        logging.error(f"データベース接続中にエラー: {e} pgcode={getattr(e, 'pgcode', '')}, detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}")
        return None

def release_db_connection(conn):
    try:
        if db_pool and conn:
            db_pool.putconn(conn)
    except Error as e:
        logging.error(f"データベース接続のリリース中にエラー: {e} pgcode={getattr(e, 'pgcode', '')}, detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}")

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
        logging.error(f"テーブルの初期化中にエラー: {e} pgcode={getattr(e, 'pgcode', '')}, detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}")
    finally:
        release_db_connection(conn)

initialize_db()

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# データベースからメッセージを取得・保存する際に参照するTHREAD_ID
THREAD_ID = 1288407362318893109
READ_LATER_REACTION_ID = 1304690617405669376
FAVORITE_REACTION_ID = 1304690627723657267
RANDOM_EXCLUDE_REACTION_ID = 1289782471197458495
SPECIAL_EXCLUDE_AUTHOR = 695096014482440244

last_chosen_authors = {}
current_panel_message_id = None  # 現在表示中のパネルメッセージID

async def get_reactions_dict(message):
    reactions = {}
    for reaction in message.reactions:
        if hasattr(reaction.emoji, 'id'):
            users = [user.id async for user in reaction.users()]
            reactions[str(reaction.emoji.id)] = users
    return reactions

async def save_message_to_db(message):
    conn = get_db_connection()
    if not conn:
        return
    try:
        reactions_dict = await get_reactions_dict(message)
        reactions_json = json.dumps(reactions_dict)
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (message_id) DO UPDATE SET reactions = EXCLUDED.reactions
            """, (
                message.id,
                THREAD_ID,
                message.author.id,
                reactions_json,
                message.content
            ))
            conn.commit()
    except Error as e:
        logging.error(f"メッセージ保存中にエラー: {e} pgcode={getattr(e, 'pgcode', '')}, detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}")
    finally:
        release_db_connection(conn)

async def bulk_save_messages_to_db(messages):
    conn = get_db_connection()
    if not conn or not messages:
        return
    try:
        data = []
        for message in messages:
            reactions_dict = await get_reactions_dict(message)
            reactions_json = json.dumps(reactions_dict)
            data.append((message.id, THREAD_ID, message.author.id, reactions_json, message.content))

        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (message_id) DO UPDATE SET reactions = EXCLUDED.reactions
            """, data)
            conn.commit()
        logging.info(f"{len(messages)}件のメッセージをバルク挿入または更新しました。")
    except Error as e:
        logging.error(f"バルク挿入中にエラー: {e} pgcode={getattr(e, 'pgcode', '')}, detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}")
    finally:
        release_db_connection(conn)

async def update_reactions_in_db(message_id):
    channel = bot.get_channel(THREAD_ID)
    if channel is None:
        logging.error(f"チャンネル {THREAD_ID} が見つかりませんでした。")
        return
    try:
        message = await channel.fetch_message(message_id)
    except discord.NotFound:
        logging.error(f"メッセージ {message_id} が見つかりませんでした。")
        return
    except discord.Forbidden:
        logging.error(f"メッセージ {message_id} へのアクセスが拒否されました。")
        return
    except discord.HTTPException as e:
        logging.error(f"メッセージ {message_id} の取得中にエラー: {e}")
        return

    await save_message_to_db(message)

def user_reacted(msg, reaction_id, user_id):
    reaction_data = msg['reactions']
    if reaction_data is None:
        reaction_data = {}
    if isinstance(reaction_data, str):
        reaction_data = json.loads(reaction_data)
    users = reaction_data.get(str(reaction_id), [])
    return user_id in users

def get_random_message(thread_id, filter_func=None):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
            messages = cur.fetchall()
            for msg in messages:
                if msg['reactions'] is None:
                    msg['reactions'] = {}
                elif isinstance(msg['reactions'], str):
                    msg['reactions'] = json.loads(msg['reactions']) or {}

            if filter_func:
                messages = [m for m in messages if filter_func(m)]
            if not messages:
                return None
            return random.choice(messages)
    except Error as e:
        logging.error(f"データベース操作中にエラー: {e} pgcode={getattr(e, 'pgcode', '')}, detail={getattr(getattr(e, 'diag', None), 'message_detail', '')}")
        return None
    finally:
        release_db_connection(conn)

async def send_panel(channel):
    global current_panel_message_id
    if current_panel_message_id:
        try:
            panel_message = await channel.fetch_message(current_panel_message_id)
            await panel_message.delete()
            logging.info(f"以前のパネルメッセージ {current_panel_message_id} を削除しました。")
        except discord.NotFound:
            logging.warning(f"以前のパネルメッセージ {current_panel_message_id} が見つかりません。")
        except discord.HTTPException as e:
            logging.error(f"パネルメッセージ {current_panel_message_id} の削除中にエラー: {e}")

    embed = create_panel_embed()
    view = CombinedView()
    try:
        sent_message = await channel.send(embed=embed, view=view)
        current_panel_message_id = sent_message.id
        logging.info(f"新しいパネルメッセージ {current_panel_message_id} を送信しました。")
    except discord.HTTPException as e:
        logging.error(f"パネルメッセージの送信中にエラー: {e}")

def is_specific_user():
    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.id == 822460191118721034
    return app_commands.check(predicate)

def create_panel_embed():
    embed = discord.Embed(
        description=(
            "🎯ｴﾛ漫画ﾙｰﾚｯﾄ\n\n"
            "botがｴﾛ漫画を選んでくれるよ！<a:c296:1288305823323263029>\n\n"
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
                    f"{interaction.user.mention} 条件に合う投稿が見つかりませんでした。"
                )
        except Exception as e:
            logging.error(f"メッセージの取得または応答中にエラーが発生しました: {e}")
            await interaction.channel.send(
                f"{interaction.user.mention} 投稿を読み込む際にエラーが発生しました。しばらくしてから再試行してください。"
            )
        finally:
            # パネルを再送信して最下部に移動
            await send_panel(interaction.channel)

    async def get_and_handle_random_message(self, interaction, filter_func):
        try:
            random_message = get_random_message(THREAD_ID, filter_func)
            await self.handle_selection(interaction, random_message)
        except Exception as e:
            logging.error(f"ボタン押下時にエラー: {e}")
            await interaction.channel.send(str(e))

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
            if msg['author_id'] == interaction.user.id:
                return False
            if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
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
            if msg['author_id'] == interaction.user.id:
                return False
            if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
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
            if msg['author_id'] == interaction.user.id:
                return False
            if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
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
            if msg['author_id'] == interaction.user.id:
                return False
            if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                return False
            return True
        await self.get_and_handle_random_message(interaction, filter_func)


@bot.tree.command(name="panel")
@is_specific_user()
async def panel(interaction: discord.Interaction):
    # コマンドを打ったチャンネル (interaction.channel) にパネルを表示
    await interaction.response.defer()
    channel = interaction.channel
    if channel is None:
        logging.error("コマンドが実行されたチャンネルを取得できませんでした。")
        await interaction.followup.send("エラーが発生しました。チャンネルが特定できません。", ephemeral=True)
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
        logging.error(f"update_dbコマンド中にエラーが発生しました: {e}")
        await interaction.followup.send(f"エラーが発生しました: {e}", ephemeral=True)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("このコマンドを実行する権限がありません。", ephemeral=True)
    else:
        logging.error(f"Unhandled exception in an app command: {error}")
        await interaction.response.send_message("エラーが発生しました。しばらくしてから再試行してください。", ephemeral=True)

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    await update_reactions_in_db(payload.message_id)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    await update_reactions_in_db(payload.message_id)

@tasks.loop(minutes=60)
async def save_all_messages_to_db_task():
    await save_all_messages_to_db()

async def save_all_messages_to_db():
    channel = bot.get_channel(THREAD_ID)
    if channel:
        try:
            limit_count = 100
            messages = []
            count = 0
            async for message in channel.history(limit=limit_count):
                messages.append(message)
                count += 1
            if messages:
                await bulk_save_messages_to_db(messages)
            logging.info(f"最大{limit_count}件のメッセージをデータベースに保存しました。")
        except discord.HTTPException as e:
            logging.error(f"メッセージ履歴の取得中にエラーが発生しました: {e}")
    else:
        logging.error("指定されたTHREAD_IDのチャンネルが見つかりません。")

@bot.event
async def on_ready():
    save_all_messages_to_db_task.start()
    logging.info(f"Botが起動しました！ {bot.user}")
    try:
        synced = await bot.tree.sync()
        logging.info(f"スラッシュコマンドが同期されました。: {synced}")
    except Exception as e:
        logging.error(f"スラッシュコマンドの同期中にエラーが発生しました: {e}")

if DISCORD_TOKEN:
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logging.error(f"Bot起動中にエラーが発生しました: {e}")
        if db_pool:
            db_pool.closeall()
            logging.info("データベース接続プールをクローズしました。")
else:
    logging.error("DISCORD_TOKENが設定されていません。")
