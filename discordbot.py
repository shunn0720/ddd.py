import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
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
bot = commands.Bot(command_prefix="!", intents=intent 1 s)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# スレッドとリアクションIDの定義
THREAD_ID = 1288407362318893109
READ_LATER_REACTION_ID = 1304759949309509672
FAVORITE_REACTION_ID = 1304759949309509673
RANDOM_EXCLUDE_REACTION_ID = 1304759949309509674

# メッセージをデータベースに保存 (バッチインサート)
def save_messages_to_db(thread_id):
    forum_channel = bot.get_channel(thread_id)
    if forum_channel is None:
        return
    thread = forum_channel.get_thread(thread_id)
    if thread:
        messages_to_insert = []
        async for message in thread.history(limit=None): 
            messages_to_insert.append((
                message.id,
                thread_id,
                message.author.id,
                str({reaction.emoji.id: reaction.count for reaction in message.reactions if hasattr(reaction.emoji, 'id')}),
                message.content
            ))

        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.executemany("""
                        INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (message_id) DO NOTHING
                        """, messages_to_insert)
                    conn.commit()
        except psycopg2.Error as e:
            print(f"Error saving messages to database: {e}")

# メッセージをランダムに取得
def get_random_message(thread_id, filter_func=None):
    with get_db_connection() as conn:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
            messages = cur.fetchall()
            if filter_func:
                messages = [msg for msg in messages if filter_func(msg)]
            return random.choice(messages) if messages else None

# ボタンのUI定義
class MangaSelectorView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary)
    async def later_read(self, interaction: discord.Interaction, button: discord.ui.Button):
        def filter_func(msg):
            reactions = msg['reactions']
            return str(READ_LATER_REACTION_ID) in reactions and int(reactions[str(READ_LATER_REACTION_ID)]) > 0

        random_message = get_random_message(THREAD_ID, filter_func)
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、<@{random_message['author_id']}> さんが投稿したこの本がおすすめだよ！\nhttps://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
            )
        else:
            await interaction.response.send_message("条件に合う投稿が見つかりませんでした。", ephemeral=True)

    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary)
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        def filter_func(msg):
            reactions = msg['reactions']
            return str(FAVORITE_REACTION_ID) in reactions and int(reactions[str(FAVORITE_REACTION_ID)]) > 0

        random_message = get_random_message(THREAD_ID, filter_func)
        if random_message:
            await interaction.response.send_message(
                f"{interaction.user.mention} さんには、<@{random_message['author_id']}> さんが投稿したこの本がおすすめだよ！\nhttps://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
            )
        else:
            await interaction.response.send_message("条件に合う投稿が見つかりませんでした。", ephemeral=True)

    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.danger)
    async def random_exclude(self, interaction: discord.Interaction, button: discord.ui.Button):
        def filter_func(msg):
            reactions = msg['reactions']
            return str(RANDOM_EXCLUDE_REACTION_ID) not in reactions

        random_message = get_random_message(THREAD_ID, filter_func)
        try:
            await interaction.response.defer(ephemeral=True)  # Acknowledge interaction

            if random_message:
                await interaction.followup.send( 
                    f"<@{random_message['author_id']}> さんが投稿したこの本がおすすめだよ！\nhttps://discord.com/channels/{interaction.guild.id}/{THREAD_ID}/{random_message['message_id']}"
                )
            else:
                await interaction.followup.send("条件に合う投稿が見つかりませんでした。")

        except Exception as e:
            print(f"Error in random_exclude: {e}")
            await interaction.followup.send("エラーが発生しました。", ephemeral=True)


# コマンド定義
@bot.tree.command(name="panel")
async def panel(interaction: discord.Interaction):
    """
    パネルを表示するコマンド。
    """
    embed = discord.Embed(
        title="🎯ｴﾛ漫画ﾙｰﾚｯﾄ",
        description=(
            "botがｴﾛ漫画を選んでくれるよ！\n\n"
            "【ランダム】：リアクションが付いていない投稿から選ぶ\n"
            "【あとで読む】：特定のリアクションが付いた投稿から選ぶ\n"
            "【お気に入り】：お気に入りのリアクションが付いた投稿から選ぶ"
        ),
        color=discord.Color.magenta()
    )
    view = MangaSelectorView()
    await interaction.response.send_message(embed=embed, view=view)

# Bot起動時にメッセージキャッシュをデータベースに保存
@bot.event
async def on_ready():
    await save_messages_to_db(THREAD_ID)
    print(f"Botが起動しました！ {bot.user}")

# リアクションの定期的な更新 (5分ごと)
@tasks.loop(minutes=5)
async def update_reactions():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # データベースからメッセージを取得し、リアクション数を更新するロジック
                # ... (ここでは省略)
    except psycopg2.Error as e:
        print(f"Error updating reactions: {e}")

@update_reactions.before_loop
async def before_update_reactions():
    await bot.wait_until_ready()

update_reactions.start()

# Botを起動
if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("DISCORD_TOKENが設定されていません。")
