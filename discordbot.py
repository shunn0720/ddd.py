import discord
import os
import logging
import psycopg2
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput

# ログの設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Discord intentsの設定
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

# 環境変数からトークンとデータベースURLを取得
TOKEN = os.getenv('DISCORD_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# チャンネルIDを設定
SOURCE_CHANNEL_IDS = [1299231408551755838, 1299231612944257036]
DESTINATION_CHANNEL_ID = 1299231533437292596
THREAD_PARENT_CHANNEL_ID = 1299231693336743996

# ボタンの選択肢とスコア
reaction_options = [
    {"label": "入ってほしい！", "style": discord.ButtonStyle.success, "score": 2, "custom_id": "type1"},
    {"label": "良い人！", "style": discord.ButtonStyle.success, "score": 1, "custom_id": "type2"},
    {"label": "微妙", "style": discord.ButtonStyle.danger, "score": -1, "custom_id": "type3"},
    {"label": "入ってほしくない", "style": discord.ButtonStyle.danger, "score": -2, "custom_id": "type4"}
]

# ボタンを押したユーザーのスレッドを追跡する辞書
user_threads = {}

# Bot設定
bot = commands.Bot(command_prefix='!', intents=intents)

def get_db_connection():
    """データベース接続を確立する。"""
    try:
        connection = psycopg2.connect(DATABASE_URL, sslmode='require')
        logger.info("データベース接続に成功しました")
        return connection
    except Exception as e:
        logger.error(f"データベース接続に失敗しました: {e}")
        raise

def save_thread_data(user_id, thread_id):
    """スレッドデータをデータベースに保存する。"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS user_threads (user_id BIGINT PRIMARY KEY, thread_id BIGINT);")
        cur.execute("INSERT INTO user_threads (user_id, thread_id) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET thread_id = EXCLUDED.thread_id;", (user_id, thread_id))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"スレッドIDの保存に失敗しました: {e}")

def fetch_thread_data(user_id):
    """指定したユーザーIDのスレッドIDをデータベースから取得する。"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT thread_id FROM user_threads WHERE user_id = %s;", (user_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return result[0] if result else None
    except Exception as e:
        logger.error(f"スレッドIDの取得に失敗しました: {e}")
        return None

# コメントを入力するためのモーダル
class CommentModal(Modal):
    def __init__(self, reaction_type, thread):
        super().__init__(title="投票画面")
        self.comment = TextInput(label="コメント", style=discord.TextStyle.paragraph, required=False)
        self.add_item(self.comment)
        self.reaction_type = reaction_type
        self.thread = thread

    async def on_submit(self, interaction: discord.Interaction):
        option = reaction_options[self.reaction_type]
        embed = discord.Embed(color=option['style'].value)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="リアクション結果", value=f"{interaction.user.display_name} が '{option['label']}' を押しました。", inline=False)
        embed.add_field(name="点数", value=f"{option['score']}点", inline=False)
        embed.add_field(name="コメント", value=self.comment.value if self.comment.value else "コメントなし", inline=False)

        try:
            await self.thread.send(embed=embed)
            await interaction.response.send_message("投票を完了しました！", ephemeral=True)
        except Exception as e:
            logger.error(f"スレッドへの送信に失敗しました: {e}")
            await interaction.response.send_message("スレッドが見つかりませんでした。", ephemeral=True)

# ボタンを作成するクラス
class ReactionButton(Button):
    def __init__(self, label, style, score, custom_id, reaction_type, thread):
        super().__init__(label=label, style=style, custom_id=custom_id)
        self.score = score
        self.thread = thread
        self.reaction_type = reaction_type

    async def callback(self, interaction: discord.Interaction):
        modal = CommentModal(self.reaction_type, self.thread)
        await interaction.response.send_modal(modal)

# Viewにボタンを追加
def create_reaction_view(thread):
    view = View(timeout=None)
    for i, option in enumerate(reaction_options):
        view.add_item(ReactionButton(label=option["label"], style=option["style"], score=option["score"], custom_id=option["custom_id"], reaction_type=i, thread=thread))
    return view

# on_message イベントでメッセージを転記してスレッドを作成
@bot.event
async def on_message(message):
    if message.author == bot.user or message.channel.id not in SOURCE_CHANNEL_IDS:
        return

    try:
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
        if not destination_channel:
            logger.error("転記先チャンネルが見つかりません。")
            return

        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.set_thumbnail(url=message.author.display_avatar.url)  # アイコンを大きく右側に表示
        embed.add_field(
            name="🌱つぼみ審査投票フォーム",
            value=(
                "必ずこのサーバーでお話した上で投票をお願いします。\n"
                "複数回投票した場合は、最新のものを反映します。\n"
                "この方の入場について、NG等意見のある方はお問い合わせください。"
            ),
            inline=False
        )

        posted_message = await destination_channel.send(embed=embed)
        logger.info(f"メッセージが転記されました: {posted_message.id}")

        thread_parent_channel = bot.get_channel(THREAD_PARENT_CHANNEL_ID)
        thread = await thread_parent_channel.create_thread(name=f"{message.author.display_name}のスレッド", auto_archive_duration=10080)
        save_thread_data(message.author.id, thread.id)
        view = create_reaction_view(thread)
        await posted_message.edit(view=view)

    except Exception as e:
        logger.error(f"スレッド作成またはメッセージ転記に失敗しました: {e}")

# Bot起動時の処理
@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user}")
    try:
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
        if not destination_channel:
            logger.error("再起動後の転記先チャンネルが見つかりません。")
            return

        async for message in destination_channel.history(limit=50):
            if message.author == bot.user and message.embeds:
                embed = message.embeds[0]
                user_id = int(embed.thumbnail.url.split("/")[-1])  # アイコンからユーザーIDを取得
                thread_id = fetch_thread_data(user_id)
                if thread_id:
                    thread = bot.get_channel(thread_id)
                    if thread:
                        view = create_reaction_view(thread)
                        await message.edit(view=view)
                        logger.info(f"再起動後にViewを再アタッチしました: {message.id}")
    except Exception as e:
        logger.error(f"View再アタッチに失敗しました: {e}")

# Botを実行
bot.run(TOKEN)
