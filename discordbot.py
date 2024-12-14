import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
import asyncio
import logging
import psycopg2
from psycopg2 import pool
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
except psycopg2.Error as e:
    logging.error(f"データベース接続プールの初期化中にエラー: {e}")
    db_pool = None

def get_db_connection():
    try:
        if db_pool:
            return db_pool.getconn()
        else:
            raise psycopg2.Error("データベース接続プールが初期化されていません。")
    except psycopg2.Error as e:
        logging.error(f"データベース接続中にエラー: {e}")
        return None

def release_db_connection(conn):
    try:
        if db_pool and conn:
            db_pool.putconn(conn)
    except psycopg2.Error as e:
        logging.error(f"データベース接続のリリース中にエラー: {e}")

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
    except psycopg2.Error as e:
        logging.error(f"テーブルの初期化中にエラー: {e}")
    finally:
        release_db_connection(conn)

initialize_db()

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

THREAD_ID = 1288407362318893109
READ_LATER_REACTION_ID = 1304690617405669376   # <:b434:1304690617405669376>
FAVORITE_REACTION_ID = 1304690627723657267     # <:b435:1304690627723657267>
RANDOM_EXCLUDE_REACTION_ID = 1289782471197458495 # <:b436:1289782471197458495>
SPECIAL_EXCLUDE_AUTHOR = 695096014482440244

last_chosen_authors = {}

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
    except psycopg2.Error as e:
        logging.error(f"メッセージ保存中にエラー: {e}")
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

async def user_has_reaction(guild: discord.Guild, message_id: int, emoji_id: int, user_id: int, channel_id: int):
    channel = guild.get_channel(channel_id)
    if channel is None:
        return False
    try:
        message = await channel.fetch_message(message_id)
    except discord.DiscordException:
        return False

    for reaction in message.reactions:
        if hasattr(reaction.emoji, 'id') and reaction.emoji.id == emoji_id:
            users = [u.id async for u in reaction.users()]
            return user_id in users
    return False

# この関数で filter_func内からリアクションをチェック
async def check_reaction(interaction: discord.Interaction, msg, emoji_id):
    # Discord APIから最新情報取得
    guild = interaction.guild
    if guild is None:
        guild = await bot.fetch_guild(interaction.guild_id)
    return await user_has_reaction(guild, msg['message_id'], emoji_id, interaction.user.id, THREAD_ID)

def get_random_message(thread_id, filter_func=None):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
            messages = cur.fetchall()
            if filter_func:
                # filter_funcを非同期にしたいが、この関数は同期なので、後で対応
                # ここでは一旦messages返却し、filter_funcはhandle_selectionで適用する
                pass
            return messages
    except psycopg2.Error as e:
        logging.error(f"データベース操作中にエラー: {e}")
        return None
    finally:
        release_db_connection(conn)

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

async def repost_panel(interaction: discord.Interaction):
    embed = create_panel_embed()
    new_view = CombinedView()
    await interaction.channel.send(embed=embed, view=new_view)  # 下に再表示（返信ではなく通常メッセージ）

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

    async def handle_selection(self, interaction, messages, filter_func):
        # filter_funcをasyncに変更してAPIからのチェックを行う
        async def async_filter(msg):
            return await filter_func(msg)

        # 非同期filter
        filtered = []
        for msg in messages:
            if await async_filter(msg):
                filtered.append(msg)

        if filtered:
            random_message = random.choice(filtered)
            last_chosen_authors[interaction.user.id] = random_message['author_id']
            author_name = await self.get_author_name(random_message['author_id'])
            # 単純なメッセージ送信(返信やfollowupではなく)
            await interaction.channel.send(
                f"{interaction.user.mention} さんには、{author_name} さんが投稿したこの本がおすすめだよ！\n"
                f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
            )
        else:
            # 条件に合う投稿なし
            await interaction.channel.send("条件に合う投稿が見つかりませんでした。")

        # Embedを再掲
        await repost_panel(interaction)

    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary, row=0)
    async def random_normal(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            messages = get_random_message(THREAD_ID)
            async def filter_func(msg):
                if msg['author_id'] == interaction.user.id:
                    return False
                if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                    return False
                if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                    return False
                return True
            await self.handle_selection(interaction, messages, filter_func)
        except Exception as e:
            await interaction.channel.send(str(e))

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary, row=0)
    async def read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            messages = get_random_message(THREAD_ID)
            async def filter_func(msg):
                if not await self.check_api_reaction(interaction, msg, READ_LATER_REACTION_ID):
                    return False
                if msg['author_id'] == interaction.user.id:
                    return False
                if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                    return False
                if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                    return False
                return True
            await self.handle_selection(interaction, messages, filter_func)
        except Exception as e:
            await interaction.channel.send(str(e))

    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary, row=0)
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            messages = get_random_message(THREAD_ID)
            async def filter_func(msg):
                if not await self.check_api_reaction(interaction, msg, FAVORITE_REACTION_ID):
                    return False
                if msg['author_id'] == interaction.user.id:
                    return False
                if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                    return False
                if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                    return False
                return True
            await self.handle_selection(interaction, messages, filter_func)
        except Exception as e:
            await interaction.channel.send(str(e))

    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.danger, row=1)
    async def random_exclude(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            messages = get_random_message(THREAD_ID)
            async def filter_func(msg):
                if await self.check_api_reaction(interaction, msg, RANDOM_EXCLUDE_REACTION_ID):
                    return False
                if msg['author_id'] == interaction.user.id:
                    return False
                if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                    return False
                if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                    return False
                return True
            await self.handle_selection(interaction, messages, filter_func)
        except Exception as e:
            await interaction.channel.send(str(e))

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.danger, row=1)
    async def conditional_read(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            messages = get_random_message(THREAD_ID)
            async def filter_func(msg):
                # b434が付いているか
                if not await self.check_api_reaction(interaction, msg, READ_LATER_REACTION_ID):
                    return False
                # b436が付いていたら除外
                if await self.check_api_reaction(interaction, msg, RANDOM_EXCLUDE_REACTION_ID):
                    return False
                if msg['author_id'] == interaction.user.id:
                    return False
                if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                    return False
                if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                    return False
                return True
            await self.handle_selection(interaction, messages, filter_func)
        except Exception as e:
            await interaction.channel.send(str(e))

    async def check_api_reaction(self, interaction: discord.Interaction, msg, emoji_id):
        # Discord APIから最新情報でリアクション付与確認
        guild = interaction.guild
        if guild is None:
            guild = await bot.fetch_guild(interaction.guild_id)
        return await user_has_reaction(guild, msg['message_id'], emoji_id, interaction.user.id, THREAD_ID)


@bot.tree.command(name="panel")
async def panel(interaction: discord.Interaction):
    embed = create_panel_embed()
    view = CombinedView()
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="update_db")
async def update_db(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    try:
        await save_all_messages_to_db()
        await interaction.followup.send("全てのメッセージをデータベースに保存しました。", ephemeral=True)
    except Exception as e:
        logging.error(f"update_dbコマンド中にエラーが発生しました: {e}")
        await interaction.followup.send(f"エラーが発生しました: {e}", ephemeral=True)

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
            count = 0
            async for message in channel.history(limit=limit_count):
                await save_message_to_db(message)
                count += 1
                if count % 10 == 0:
                    logging.info(f"{count}件のメッセージを処理中...")
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

@bot.event
async def on_shutdown():
    if save_all_messages_to_db_task.is_running():
        save_all_messages_to_db_task.cancel()
        logging.info("バックグラウンドタスクを停止しました。")
    if db_pool:
        db_pool.closeall()
        logging.info("データベース接続プールをクローズしました。")

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
