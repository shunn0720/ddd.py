import discord
import os
import logging
import psycopg2
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
import time

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

def get_db_connection(retries=3, delay=2):
    """データベース接続を確立する。接続に失敗した場合、指定回数リトライを試みる。"""
    attempt = 0
    while attempt < retries:
        try:
            connection = psycopg2.connect(DATABASE_URL, sslmode='require')
            logger.info("データベース接続に成功しました")
            return connection
        except psycopg2.OperationalError as e:
            logger.warning(f"データベース接続に失敗しました (試行 {attempt + 1}/{retries})。エラー: {e}")
            attempt += 1
            if attempt < retries:
                logger.info(f"{delay}秒後に再試行します...")
                time.sleep(delay)
        except Exception as e:
            logger.error(f"予期しないエラーが発生しました: {e}")
            break

    logger.critical("データベース接続に失敗しました。指定された回数のリトライを試みましたが、接続できませんでした。")
    raise RuntimeError("データベース接続を確立できませんでした。")

# コメントを入力するためのモーダル
class CommentModal(Modal):
    def __init__(self, reaction_type, thread):
        super().__init__(title="投票画面", timeout=None)

        self.comment = TextInput(
            label="コメント",
            style=discord.TextStyle.paragraph,
            placeholder="理由がある場合はこちらに入力してください（そのまま送信も可）",
            required=False
        )
        self.add_item(self.comment)
        self.reaction_type = reaction_type  # 修正: 'type' -> 'reaction_type'
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
        except AttributeError:
            logger.error("スレッドが見つかりません。")
            await interaction.response.send_message("スレッドが見つかりませんでした。", ephemeral=True)

# ボタンを作成するクラス
class ReactionButton(Button):
    def __init__(self, label, style, score, custom_id, reaction_type, thread):
        super().__init__(label=label, style=style, custom_id=custom_id)
        self.style = style
        self.score = score
        self.thread = thread
        self.reaction_type = reaction_type  # 修正: 'type' -> 'reaction_type'

    async def callback(self, interaction: discord.Interaction):
        modal = CommentModal(self.reaction_type, self.thread)
        await interaction.response.send_modal(modal)

# Viewにボタンを追加
def create_reaction_view(thread):
    view = View(timeout=None)  # timeout=Noneでボタンが消えないように設定
    for i, option in enumerate(reaction_options):
        view.add_item(ReactionButton(label=option["label"], style=option["style"], score=option["score"], custom_id=option["custom_id"], reaction_type=i, thread=thread))
    return view

# on_message イベントでメッセージを転記してスレッドを作成
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.channel.id not in SOURCE_CHANNEL_IDS:
        return

    try:
        # メッセージを転記するチャンネル
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
        if destination_channel is None:
            logger.error("転記先チャンネルが見つかりません。")
            return

        # メッセージを転記
        posted_message = await destination_channel.send(content=message.content)
        logger.info(f"メッセージが転記されました: {posted_message.id}")

        # スレッドを作成
        thread = await posted_message.create_thread(name=f"{message.author.display_name}のスレッド")
        logger.info(f"スレッドが作成されました: {thread.id} for {message.author.display_name}")

        # Viewを追加して投票ボタンを表示
        view = create_reaction_view(thread)
        await posted_message.edit(view=view)

    except Exception as e:
        logger.error(f"スレッド作成またはメッセージ転記に失敗しました: {e}")

# Bot起動時の処理
@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user.name}#{bot.user.discriminator}")
    try:
        # 再起動時にViewを再アタッチ
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
        if destination_channel is None:
            logger.error("再起動後の転記先チャンネルが見つかりません。")
            return

        async for message in destination_channel.history(limit=50):
            if message.author == bot.user and message.thread:
                view = create_reaction_view(message.thread)
                await message.edit(view=view)
                logger.info(f"再起動後にViewを再アタッチしました: {message.id}")
    except Exception as e:
        logger.error(f"View再アタッチに失敗しました: {e}")

# Botを実行
bot.run(TOKEN)
