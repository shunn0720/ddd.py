import os
import discord
from discord.ext import commands
from discord import app_commands
import random
import logging
import psycopg2
from psycopg2.extras import DictCursor

# ログ設定
logging.basicConfig(level=logging.ERROR, filename="bot_errors.log", filemode="a", format="%(asctime)s - %(levelname)s - %(message)s")

# 環境変数からデータベースURLを取得
DATABASE_URL = os.getenv("DATABASE_URL")

# データベース接続
def get_db_connection():
    try:
        return psycopg2.connect(DATABASE_URL, sslmode="require")
    except psycopg2.Error as e:
        logging.error(f"Database connection error: {str(e)}")
        raise

# テーブル初期化
def initialize_db():
    try:
        with get_db_connection() as conn:
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
    except psycopg2.Error as e:
        logging.error(f"Database initialization error: {str(e)}")

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
REACTION_ID = 1304759949309509672

# メッセージ保存
async def save_messages_to_db(thread_id):
    try:
        thread = bot.get_channel(thread_id)
        if thread is None:
            logging.error(f"Thread not found for ID: {thread_id}")
            return

        async for message in thread.history(limit=100):
            reactions = {
                str(reaction.emoji.id): reaction.count
                for reaction in message.reactions if hasattr(reaction.emoji, "id")
            }
            try:
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                        INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (message_id) DO UPDATE SET reactions = EXCLUDED.reactions
                        """, (message.id, thread_id, message.author.id, str(reactions), message.content))
                        conn.commit()
            except psycopg2.Error as e:
                logging.error(f"Error saving message {message.id} to database: {str(e)}")
    except Exception as e:
        logging.error(f"Error in save_messages_to_db: {str(e)}")

# ランダムメッセージ取得
def get_random_message(thread_id, filter_func, user_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
                messages = cur.fetchall()

                if filter_func:
                    messages = [msg for msg in messages if filter_func(msg, user_id)]

                if not messages:
                    return None  # 条件に合うメッセージがない場合

                return random.choice(messages)
    except psycopg2.Error as e:
        logging.error(f"Database error in get_random_message: {str(e)}")
        return None

# ボタンのUI定義
class MangaSelectorView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary)
    async def random_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        random_message = get_random_message(THREAD_ID, lambda msg, user_id: msg["author_id"] != user_id, user_id)
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、<@{random_message['author_id']}> さんが投稿したこの本がおすすめだよ！\n"
                f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
            )
        else:
            await interaction.response.send_message("エッチだなっつ！", ephemeral=True)

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary)
    async def later_read_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        random_message = get_random_message(
            THREAD_ID,
            lambda msg, user_id: str(REACTION_ID) in msg["reactions"] and user_id in [reaction["user_id"] for reaction in msg["reactions"]],
            user_id
        )
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、<@{random_message['author_id']}> さんが投稿したこの本がおすすめだよ！\n"
                f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
            )
        else:
            await interaction.response.send_message("エッチだなっつ！", ephemeral=True)

    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary)
    async def favorite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        random_message = get_random_message(
            THREAD_ID,
            lambda msg, user_id: str(REACTION_ID) in msg["reactions"] and user_id in [reaction["user_id"] for reaction in msg["reactions"]],
            user_id
        )
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、<@{random_message['author_id']}> さんが投稿したこの本がおすすめだよ！\n"
                f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
            )
        else:
            await interaction.response.send_message("エッチだなっつ！", ephemeral=True)

    @discord.ui.button(label="ランダム除外", style=discord.ButtonStyle.danger)
    async def random_exclude_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        random_message = get_random_message(
            THREAD_ID,
            lambda msg, user_id: str(REACTION_ID) not in msg["reactions"] and msg["author_id"] != user_id,
            user_id
        )
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、<@{random_message['author_id']}> さんが投稿したこの本がおすすめだよ！\n"
                f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
            )
        else:
            await interaction.response.send_message("エッチだなっつ！", ephemeral=True)

    @discord.ui.button(label="あとで読む (条件付き)", style=discord.ButtonStyle.danger)
    async def later_read_conditional_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        random_message = get_random_message(
            THREAD_ID,
            lambda msg, user_id: str(REACTION_ID) in msg["reactions"] and user_id in [reaction["user_id"] for reaction in msg["reactions"]],
            user_id
        )
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、<@{random_message['author_id']}> さんが投稿したこの本がおすすめだよ！\n"
                f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
            )
        else:
            await interaction.response.send_message("エッチだなっつ！", ephemeral=True)

# コマンド定義
@bot.tree.command(name="panel")
async def panel(interaction: discord.Interaction):
    """
    パネルを表示するコマンド。
    """
    embed = discord.Embed(
        title="🎯ｴﾛ漫画ﾙｰﾚｯﾄ",
        description=(
            "botがｴﾛ漫画を選んでくれるよ！<a:c296:1288305823323263029>\n\n"
            "🔵：自分の<:b431:1289782471197458495>を除外しない\n"
            "🔴：自分の<:b431:1289782471197458495>を除外する\n\n"
            "【ランダム】　：全体から選ぶ\n"
            "【あとで読む】：<:b434:1304690617405669376>を付けた投稿から選ぶ\n"
            "【お気に入り】：<:b435:1304690627723657267>を付けた投稿から選ぶ"
        ),
        color=discord.Color.magenta()
    )
    view = MangaSelectorView()
    await interaction.response.send_message(embed=embed, view=view)

# 起動時の処理
@bot.event
async def on_ready():
    await save_messages_to_db(THREAD_ID)
    print(f"Botが起動しました！ {bot.user}")

# Botを起動
if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("DISCORD_TOKENが設定されていません。")
