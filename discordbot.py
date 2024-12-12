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

# カスタム例外定義
class DatabaseOperationError(Exception):
    pass

# 環境変数の読み込み
load_dotenv()

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
SPECIAL_EXCLUDE_AUTHOR = 695096014482440244  # 特定の投稿者IDを除外

# ユーザーごとに最後に選ばれた投稿者を記録する辞書
last_chosen_authors = {}

# 非同期でリアクションの辞書を取得する関数
async def get_reactions_dict(message):
    reactions = {}
    for reaction in message.reactions:
        if hasattr(reaction.emoji, 'id'):  # カスタム絵文字の場合
            users = [user.id async for user in reaction.users()]
            reactions[str(reaction.emoji.id)] = users
    return reactions

# メッセージをデータベースに保存
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

# データベースのリアクション情報を更新
async def update_reactions_in_db(message_id):
    # メッセージを取得してDB更新
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

    # メッセージのリアクション情報を更新
    await save_message_to_db(message)

# ユーザーが特定のリアクションを付けているか確認
def user_reacted(msg, reaction_id, user_id):
    reaction_data = msg['reactions']
    # reactionsがNoneの場合は空dictとして扱う
    if reaction_data is None:
        reaction_data = {}

    if isinstance(reaction_data, str):
        reaction_data = json.loads(reaction_data)

    users = reaction_data.get(str(reaction_id), [])
    return user_id in users

# メッセージをランダムに取得
def get_random_message(thread_id, filter_func=None):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
            messages = cur.fetchall()
            for msg in messages:
                # reactionsがstrの場合はJSONデコード、Noneの場合は空dict
                if msg['reactions'] is None:
                    msg['reactions'] = {}
                elif isinstance(msg['reactions'], str):
                    msg['reactions'] = json.loads(msg['reactions']) or {}

            if filter_func:
                messages = [msg for msg in messages if filter_func(msg)]
            if not messages:
                raise ValueError("指定された条件に合うメッセージがありませんでした。")
            return random.choice(messages)
    except psycopg2.Error as e:
        logging.error(f"データベース操作中にエラー: {e}")
        raise DatabaseOperationError(f"データベースエラーが発生しました: {e}")
    except ValueError as e:
        # 見つからなかった場合はInfoレベルで出力
        logging.info(str(e))
        raise
    finally:
        release_db_connection(conn)


class CombinedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # 上段（青ボタン）
    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary, row=0)
    async def random_normal(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            def filter_func(msg):
                if msg['author_id'] == interaction.user.id:
                    return False
                if msg['author_id'] == SPECIAL_EXCLUDE_AUTHOR:
                    return False
                if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                    return False
                return True

            random_message = get_random_message(THREAD_ID, filter_func)
            await self.handle_selection(interaction, random_message)
        except Exception as e:
            await interaction.response.send_message(str(e), ephemeral=True)

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary, row=0)
    async def read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
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

            random_message = get_random_message(THREAD_ID, filter_func)
            await self.handle_selection(interaction, random_message)
        except Exception as e:
            await interaction.response.send_message(str(e), ephemeral=True)

    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary, row=0)
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
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

            random_message = get_random_message(THREAD_ID, filter_func)
            await self.handle_selection(interaction, random_message)
        except Exception as e:
            await interaction.response.send_message(str(e), ephemeral=True)

    # 下段（赤ボタン）
    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.danger, row=1)
    async def random_exclude(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
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

            random_message = get_random_message(THREAD_ID, filter_func)
            await self.handle_selection(interaction, random_message)
        except Exception as e:
            await interaction.response.send_message(str(e), ephemeral=True)

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.danger, row=1)
    async def conditional_read(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
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

            random_message = get_random_message(THREAD_ID, filter_func)
            await self.handle_selection(interaction, random_message)
        except Exception as e:
            await interaction.response.send_message(str(e), ephemeral=True)

    async def handle_selection(self, interaction, random_message):
        if random_message:
            last_chosen_authors[interaction.user.id] = random_message['author_id']
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、<@{random_message['author_id']}> さんが投稿したこの本がおすすめだよ！\n"
                f"https://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
            )
            # ボタンを押す度に再度パネルを下部へ送信
            await self.repost_panel(interaction)
        else:
            await interaction.response.send_message("条件に合う投稿が見つかりませんでした。", ephemeral=True)

    async def repost_panel(self, interaction):
        embed = create_panel_embed()
        new_view = CombinedView()
        await interaction.followup.send(embed=embed, content=create_table_layout(), view=new_view)


def create_panel_embed():
    embed = discord.Embed(
        title="🎯エロ漫画ルーレット",
        description=(
            "botがエロ漫画を選んでくれるよ！<a:c296:1288305823323263029>\n"
            "■ 青ボタン（上段）\n"
            "【ランダム】：全体からランダムで1つ\n"
            "【あとで読む】：<:b434:1304690617405669376>を付けた自分用の投稿から\n"
            "【お気に入り】：<:b435:1304690627723657267>を付けた自分用の投稿から\n\n"
            "■ 赤ボタン（下段）\n"
            "【ランダム除外】：<:b436:1304763661172346973>を付けた自分用の投稿は除外\n"
            "【条件付き読む】：あとで読む付き＆ランダム除外なしの自分用投稿から"
        ),
        color=discord.Color.magenta()
    )
    return embed

def create_table_layout():
    return (
        "```\n"
        "+------------------------------------------------+\n"
        "|               【ランダム】【あとで読む】【お気に入り】             |\n"
        "+------------------------------------------------+\n"
        "|               【ランダム除外】【条件付き読む】                 |\n"
        "+------------------------------------------------+\n"
        "```"
    )

# /panel コマンド
@bot.tree.command(name="panel")
async def panel(interaction: discord.Interaction):
    embed = create_panel_embed()
    view = CombinedView()
    await interaction.response.send_message(embed=embed, content=create_table_layout(), view=view)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    # リアクションが削除されたとき
    # メッセージIDを元にDB内のリアクション情報を更新する
    await update_reactions_in_db(payload.message_id)

@tasks.loop(minutes=60)
async def save_all_messages_to_db_task():
    await save_all_messages_to_db()

async def save_all_messages_to_db():
    # 必要に応じて全メッセージ保存処理を実装
    pass

@bot.event
async def on_ready():
    save_all_messages_to_db_task.start()
    logging.info(f"Botが起動しました！ {bot.user}")

@bot.event
async def on_shutdown():
    if save_all_messages_to_db_task.is_running():
        save_all_messages_to_db_task.cancel()
        logging.info("バックグラウンドタスクを停止しました。")
    if db_pool:
        db_pool.closeall()
        logging.info("データベース接続プールをクローズしました。")

# Botを起動
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
