import discord
import os
import logging
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput

# ログの設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

# Herokuの環境変数からトークンを取得
TOKEN = os.getenv('DISCORD_TOKEN')

# チャンネルIDを設定
SOURCE_CHANNEL_IDS = [1299231408551755838, 1299231612944257036]
DESTINATION_CHANNEL_ID = 1299231533437292596
THREAD_PARENT_CHANNEL_ID = 1299231693336743996

# ボタンの選択肢とスコア
reaction_options = [
    {"label": "入ってほしい！", "color": discord.Color.green(), "score": 2, "custom_id": "type1"},
    {"label": "良い人！", "color": discord.Color.green(), "score": 1, "custom_id": "type2"},
    {"label": "微妙", "color": discord.Color.red(), "score": -1, "custom_id": "type3"},
    {"label": "入ってほしくない", "color": discord.Color.red(), "score": -2, "custom_id": "type4"}
]

user_threads = {}

# Bot設定
bot = commands.Bot(command_prefix='!', intents=intents)

# ReactionButton クラス
class ReactionButton(Button):
    def __init__(self, label, color, score, thread):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.label = label
        self.color = color
        self.score = score
        self.thread = thread

    async def callback(self, interaction: discord.Interaction):
        modal = CommentModal(self.label, self.color, self.score, self.thread)
        await interaction.response.send_modal(modal)

# コメントモーダル
class CommentModal(Modal):
    def __init__(self, label, color, score, thread):
        super().__init__(title="投票画面")
        self.label = label
        self.color = color
        self.score = score
        self.thread = thread
        self.comment = TextInput(
            label="コメント",
            style=discord.TextStyle.paragraph,
            placeholder="理由がある場合はこちらに入力してください（そのまま送信も可）",
            required=False
        )
        self.add_item(self.comment)

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(color=self.color)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.add_field(
            name="リアクション結果",
            value=f"{interaction.user.display_name} が '{self.label}' を押しました。",
            inline=False
        )
        embed.add_field(
            name="点数",
            value=f"{self.score}点",
            inline=False
        )
        embed.add_field(
            name="コメント",
            value=self.comment.value if self.comment.value else "コメントなし",
            inline=False
        )
        await self.thread.send(embed=embed)
        await interaction.response.send_message("投票を記録しました。", ephemeral=True)

# Viewにボタンを追加
def create_reaction_view(user_id):
    view = View(timeout=7 * 24 * 60 * 60)  # 7日後にタイムアウト
    thread = user_threads.get(user_id)
    for option in reaction_options:
        view.add_item(ReactionButton(label=option["label"], color=option["color"], score=option["score"], thread=thread))
    view.on_timeout = lambda: disable_view(view)
    return view

# ボタンを無効化する関数
async def disable_view(view):
    for item in view.children:
        item.disabled = True
    if view.message:
        await view.message.edit(view=view)

# on_message イベントでメッセージを転記
@bot.event
async def on_message(message):
    if message.channel.id in SOURCE_CHANNEL_IDS and not message.author.bot:
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)

        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(name=message.author.display_name)
        embed.set_thumbnail(url=message.author.display_avatar.url)
        embed.add_field(
            name="🌱つぼみ審査投票フォーム",
            value=(
                "必ずこのサーバーでお話した上で投票をお願いします。\n"
                "複数回投票した場合は、最新のものを反映します。\n"
                "この方の入場について、NG等意見のある方はお問い合わせください。"
            ),
            inline=False
        )

        view = create_reaction_view(message.author.id)
        sent_message = await destination_channel.send(embed=embed, view=view)
        view.message = sent_message

        logger.info(f"メッセージが転記されました: {sent_message.id}")

        # スレッド作成
        thread_parent_channel = bot.get_channel(THREAD_PARENT_CHANNEL_ID)
        thread = await thread_parent_channel.create_thread(
            name=f"{message.author.display_name}のリアクション投票スレッド",
            auto_archive_duration=10080  # 7日
        )
        user_threads[message.author.id] = thread
        logger.info(f"スレッドが作成されました: {thread.id} for {message.author.display_name}")

# 再起動後にViewを再アタッチ
@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user}')
    destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
    async for message in destination_channel.history(limit=50):
        if message.author == bot.user and message.embeds:
            try:
                if message.embeds[0].thumbnail and message.embeds[0].thumbnail.url:
                    user_id = int(message.embeds[0].thumbnail.url.split("/")[-2])
                    view = create_reaction_view(user_id)
                    view.message = message  # タイムアウト時の編集用
                    await message.edit(view=view)
                    logger.info(f"再起動後にViewを再アタッチしました: {message.id}")
            except Exception as e:
                logger.error(f"再アタッチに失敗しました: {e}")

bot.run(TOKEN)
