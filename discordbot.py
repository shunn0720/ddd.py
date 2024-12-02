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

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

# DATABASE_URL 環境変数を取得
DATABASE_URL = os.getenv("DATABASE_URL")

# コネクションプールの初期化
try:
    db_pool = pool.SimpleConnectionPool(
        minconn=1, maxconn=10, dsn=DATABASE_URL, sslmode='require'
    )
except psycopg2.Error as e:
    logging.error(f"データベース接続プールの初期化中にエラー: {e}")
    db_pool = None

# データベース接続を取得
def get_db_connection():
    try:
        if db_pool:
            return db_pool.getconn()
        else:
            raise psycopg2.Error("データベース接続プールが初期化されていません。")
    except psycopg2.Error as e:
        logging.error(f"データベース接続中にエラー: {e}")
        return None

# データベース接続をリリース
def release_db_connection(conn):
    try:
        if db_pool and conn:
            db_pool.putconn(conn)
    except psycopg2.Error as e:
        logging.error(f"データベース接続のリリース中にエラー: {e}")

# テーブルの初期化
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

# Bot設定
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# スレッドとリアクションIDの定義
THREAD_ID = 1288407362318893109
READ_LATER_REACTION_ID = 1304690617405669376
FAVORITE_REACTION_ID = 1304690627723657267
RANDOM_EXCLUDE_REACTION_ID = 1304763661172346973

# メッセージをデータベースに保存
async def save_message_to_db(message):
    conn = get_db_connection()
    if not conn:
        return
    try:
        reactions_dict = {
            str(reaction.emoji.id): [user.id async for user in reaction.users()]
            for reaction in message.reactions if hasattr(reaction.emoji, 'id')
        }
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (message_id) DO UPDATE SET reactions = EXCLUDED.reactions
            """, (
                message.id,
                THREAD_ID,
                message.author.id,
                str(reactions_dict),
                message.content
            ))
            conn.commit()
    except psycopg2.Error as e:
        logging.error(f"メッセージ保存中にエラー: {e}")
    finally:
        release_db_connection(conn)

# メッセージをランダムに取得
def get_random_message(thread_id, filter_func=None):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
            messages = cur.fetchall()
            if filter_func:
                messages = [msg for msg in messages if filter_func(msg)]
            if not messages:
                raise ValueError("条件に合うメッセージが見つかりませんでした。")  # 例外を発生させる
            return random.choice(messages)
    except (psycopg2.Error, ValueError) as e:
        logging.error(f"メッセージ取得中にエラー: {e}")
        return None  # エラーが発生した場合はNoneを返す
    finally:
        release_db_connection(conn)

# ボタンのUI定義
class MangaSelectorView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary)
    async def random_normal(self, interaction: discord.Interaction, button: discord.ui.Button):
        random_message = get_random_message(THREAD_ID, lambda msg: msg['author_id'] != interaction.user.id)
        await self.send_random_message(interaction, random_message)

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary)
    async def read_later_normal(self, interaction: discord.Interaction, button: discord.ui.Button):
        random_message = get_random_message(
            THREAD_ID,
            lambda msg: msg['author_id'] != interaction.user.id and str(READ_LATER_REACTION_ID) in msg['reactions']
        )
        await self.send_random_message(interaction, random_message)

    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary)
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        random_message = get_random_message(
            THREAD_ID,
            lambda msg: msg['author_id'] != interaction.user.id and str(FAVORITE_REACTION_ID) in msg['reactions']
        )
        await self.send_random_message(interaction, random_message)

    @discord.ui.button(label="ランダム除外", style=discord.ButtonStyle.danger)
    async def random_exclude(self, interaction: discord.Interaction, button: discord.ui.Button):
        random_message = get_random_message(
            THREAD_ID,
            lambda msg: msg['author_id'] != interaction.user.id and str(RANDOM_EXCLUDE_REACTION_ID) not in msg['reactions']
        )
        await self.send_random_message(interaction, random_message)

    @discord.ui.button(label="あとで読む, style=discord.ButtonStyle.danger)
    async def read_later_exclude(self, interaction: discord.Interaction, button: discord.ui.Button):
        random_message = get_random_message(
            THREAD_ID,
            lambda msg: msg['author_id'] != interaction.user.id
            and str(READ_LATER_REACTION_ID) in msg['reactions']
            and str(RANDOM_EXCLUDE_REACTION_ID) not in msg['reactions']
        )
        await self.send_random_message(interaction, random_message)

    async def send_random_message(self, interaction, random_message):
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、<@{random_message['author_id']}> さんが投稿したこの本がおすすめだよ！\n"
                f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
            )
        else:
            await interaction.response.send_message("条件に合う投稿が見つかりませんでした。", ephemeral=True)

# コマンド定義
@bot.tree.command(name="panel")
async def panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎯ｴﾛ漫画ﾙｰﾚｯﾄ",
        description=(
            "botがｴﾛ漫画を選んでくれるよ！<a:c296:1288305823323263029>\n\n"
            "🔵：自分の<:b431:1289782471197458495>を除外しない\n"
            "🔴：自分の<:b431:1289782471197458495>を除外する\n\n"
            "【ランダム】：全体から選ぶ\n"
            "【あとで読む】：<:b434:1304690617405669376>を付けた投稿から選ぶ\n"
            "【お気に入り】：<:b435:1304690627723657267>を付けた投稿から選ぶ"
        ),
        color=discord.Color.magenta()
    )
    view = MangaSelectorView()
    await interaction.response.send_message(embed=embed, view=view)

# メッセージ削除時の処理
@bot.event
async def on_raw_message_delete(payload):
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM messages WHERE message_id = %s", (payload.message_id,))
            conn.commit()
        logging.info(f"メッセージ {payload.message_id} が削除されました。")
    except psycopg2.Error as e:
        logging.error(f"メッセージ削除中にエラー: {e}")
    finally:
        release_db_connection(conn)

# リアクションの定期的な更新
@tasks.loop(minutes=5)
async def update_reactions():
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT message_id FROM messages")
            message_ids = [row[0] for row in cur.fetchall()]

            thread = bot.get_channel(THREAD_ID)
            for message_id in message_ids:
                try:
                    message = await thread.fetch_message(message_id)
                    reactions_dict = {
                        str(reaction.emoji.id): reaction.count
                        for reaction in message.reactions if hasattr(reaction.emoji, 'id')
                    }
                    with conn.cursor() as cur:
                        cur.execute("UPDATE messages SET reactions = %s WHERE message_id = %s",
                                    (str(reactions_dict), message_id))
                        conn.commit()

                    await asyncio.sleep(1)  # レート制限対策を強化
                except discord.NotFound:
                    logging.warning(f"Message not found: {message_id}")
                except Exception as e:
                    logging.error(f"Error updating reactions for message {message_id}: {e}")
    except psycopg2.Error as e:
        logging.error(f"Error updating reactions: {e}")
    finally:
        release_db_connection(conn)

# Bot起動時の処理
@bot.event
async def on_ready():
    await save_messages_to_db()
    update_reactions.start()
    logging.info(f"Botが起動しました！ {bot.user}")

# 新しいメッセージを保存
@bot.event
async def on_message(message):
    if message.channel.id == THREAD_ID:
        await save_message_to_db(message)

# リアクションが追加されたときに更新
@bot.event
async def on_raw_reaction_add(payload):
    try:
        if payload.channel_id == THREAD_ID:
            thread = bot.get_channel(payload.channel_id)
            if thread:
                message = await thread.fetch_message(payload.message_id)
                await save_message_to_db(message)
    except Exception as e:
        print(f"Error updating reactions for message {payload.message_id}: {str(e)}")

# Botを起動
if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    logging.error("DISCORD_TOKENが設定されていません。")
