import discord
import os
import logging
import psycopg2
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput

# ログの設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

# Herokuの環境変数からトークンとデータベースURLを取得
DATABASE_URL = os.getenv("DATABASE_URL")
TOKEN = os.getenv("TOKEN")

# チャンネルIDを設定
SOURCE_CHANNEL_IDS = [1289481073259970592]
DESTINATION_CHANNEL_ID = 1290017703456804958
THREAD_PARENT_CHANNEL_ID = 1289867786180624496 

# ボタンの選択肢とスコア
reaction_options = [
    {"label": "入ってほしい！", "color": discord.Color.green(), "score": 2, "custom_id": "type1"},
    {"label": "良い人！", "color": discord.Color.green(), "score": 1, "custom_id": "type2"},
    {"label": "微妙", "color": discord.Color.red(), "score": -1, "custom_id": "type3"},
    {"label": "入ってほしくない", "color": discord.Color.red(), "score": -2, "custom_id": "type4"}
]

def init_db():
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='disable')
        with conn.cursor() as cur:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS user_threads (
                    user_id BIGINT PRIMARY KEY,
                    thread_id BIGINT NOT NULL
                )
            ''')
        conn.commit()
        print("Database initialized")
    except Exception as e:
        print(f"Error initializing database: {e}")
    finally:
        if conn:
            conn.close()

# スレッドデータをデータベースに保存
def save_thread_data(user_id, thread_id):
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='disable')
        with conn.cursor() as cur:
            cur.execute('''
                INSERT INTO user_threads (user_id, thread_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE
                SET thread_id = EXCLUDED.thread_id
            ''', (user_id, thread_id))
        conn.commit()
        print(f"Thread data saved: {user_id}, {thread_id}")
    except Exception as e:
        print(f"Error saving thread data: {e}")
    finally:
        if conn:
            conn.close()

# スレッドデータをデータベースから取得
def get_thread_data(user_id):
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='disable')
        with conn.cursor() as cur:
            cur.execute('''
                SELECT thread_id FROM user_threads WHERE user_id = %s
            ''', (user_id,))
            result = cur.fetchone()
            return result[0] if result else None
    except Exception as e:
        print(f"スレッドデータの取得に失敗しました: {e}")
        return None
    finally:
        if conn:
            conn.close()

# Bot設定
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user}')
    init_db()

# 他のBotイベントコードは省略

# Botの起動
bot.run(TOKEN)
