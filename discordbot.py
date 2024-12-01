import os
import discord
from discord.ext import commands
import random
import psycopg2
from psycopg2.extras import DictCursor

# DATABASE_URL 環境変数を取得
DATABASE_URL = os.getenv("DATABASE_URL")

# データベースに接続
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

# テーブルの初期化
def initialize_db():
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

initialize_db()

# Bot設定
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# スレッドIDとリアクションID
THREAD_ID = 1288407362318893109
REACTION_IDS = {
    "read_later": 1304759949309509672,
    "favorite": 1290690173046362224,
    "exclude_random": 1310824310348316753
}
EXCLUDE_USER_ID = 695096014482440244

# ユーザーごとに最新の投稿者を記録
last_author_map = {}

# メッセージをデータベースに保存
async def save_messages_to_db(thread_id):
    channel = bot.get_channel(THREAD_ID)
    if channel:
        thread = channel.get_thread(thread_id)
        if thread:
            async for message in thread.history(limit=100):
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                        INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (message_id) DO NOTHING
                        """, (
                            message.id,
                            thread_id,
                            message.author.id,
                            str({reaction.emoji: reaction.count for reaction in message.reactions}),
                            message.content
                        ))
                        conn.commit()

# ランダムにメッセージを取得
def get_random_message(thread_id, user_id, filter_func=None):
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
                messages = cur.fetchall()

                # 自分の投稿と指定した投稿者の投稿を除外
                messages = [msg for msg in messages if msg['author_id'] != user_id and msg['author_id'] != EXCLUDE_USER_ID]

                # 前回の投稿者を除外
                last_author = last_author_map.get(user_id)
                if last_author:
                    messages = [msg for msg in messages if msg['author_id'] != last_author]

                if filter_func:
                    messages = [msg for msg in messages if filter_func(msg)]

                return random.choice(messages) if messages else None
    except Exception as e:
        print(f"Error in get_random_message: {e}")
        return "エラーだなっつ！"

# ボタンのUI定義
class MangaSelectorView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary)
    async def random(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        random_message = get_random_message(THREAD_ID, user_id)
        if isinstance(random_message, str):  # エラーメッセージ
            await interaction.response.send_message(random_message, ephemeral=True)
        else:
            last_author_map[user_id] = random_message['author_id']
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、<@{random_message['author_id']}> さんが投稿したこの本がおすすめだよ！\nhttps://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
            )

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary)
    async def read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        def filter_func(msg):
            return str(REACTION_IDS['read_later']) in msg['reactions'] and user_id in msg['reactions'][str(REACTION_IDS['read_later'])]
        random_message = get_random_message(THREAD_ID, user_id, filter_func)
        if isinstance(random_message, str):  # エラーメッセージ
            await interaction.response.send_message(random_message, ephemeral=True)
        else:
            last_author_map[user_id] = random_message['author_id']
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、<@{random_message['author_id']}> さんが投稿したこの本がおすすめだよ！\nhttps://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
            )

    # 同様に「お気に入り」「ランダム除外」「あとで読む（条件付き）」ボタンを定義

# Bot起動時の処理
@bot.event
async def on_ready():
    await save_messages_to_db(THREAD_ID)
    print(f"Botが起動しました！ {bot.user}")

# Botを起動
if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("DISCORD_TOKENが設定されていません。")
