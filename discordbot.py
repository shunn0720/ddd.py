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

# 環境変数からトークンを取得
TOKEN = os.getenv('DISCORD_TOKEN')

# チャンネルIDを設定
SOURCE_CHANNEL_IDS = [1299231408551755838, 1299231612944257036]  # ソース元チャンネル
DESTINATION_CHANNEL_ID = 1299231533437292596  # 転記先チャンネル
THREAD_PARENT_CHANNEL_ID = 1299231693336743996  # スレッドが作成されるチャンネル

# ボタンの選択肢とスコア
reaction_options = [
    {"label": "入ってほしい！", "color": discord.Color.green(), "score": 2, "custom_id": "type1"},
    {"label": "良い人！", "color": discord.Color.green(), "score": 1, "custom_id": "type2"},
    {"label": "微妙", "color": discord.Color.red(), "score": -1, "custom_id": "type3"},
    {"label": "入ってほしくない", "color": discord.Color.red(), "score": -2, "custom_id": "type4"}
]

# ボタンを押したユーザーのスレッドを追跡する辞書
user_threads = {}

# Bot設定
bot = commands.Bot(command_prefix='!', intents=intents)

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
        self.reaction_type = reaction_type
        self.thread = thread

    async def on_submit(self, interaction: discord.Interaction):
        option = reaction_options[self.reaction_type]
        embed = discord.Embed(color=option['color'])
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
    def __init__(self, label, color, score, custom_id, reaction_type, thread):
        super().__init__(label=label, style=discord.ButtonStyle.primary, custom_id=custom_id)
        self.color = color
        self.score = score
        self.reaction_type = reaction_type
        self.thread = thread

    async def callback(self, interaction: discord.Interaction):
        modal = CommentModal(self.reaction_type, self.thread)
        await interaction.response.send_modal(modal)

# Viewにボタンを追加
def create_reaction_view(thread):
    view = View(timeout=None)  # timeout=Noneでボタンが消えないように設定
    for i, option in enumerate(reaction_options):
        view.add_item(ReactionButton(label=option["label"], color=option["color"], score=option["score"], custom_id=option["custom_id"], reaction_type=i, thread=thread))
    return view

# on_message イベントでメッセージを転記してスレッドを作成
@bot.event
async def on_message(message):
    if message.author == bot.user or message.channel.id not in SOURCE_CHANNEL_IDS:
        return

    try:
        # 転記先チャンネル
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
        if destination_channel is None:
            logger.error("転記先チャンネルが見つかりません。")
            return

        # メッセージの送信者のEmbedを作成して転記
        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.add_field(
            name="🌱つぼみ審査投票フォーム",
            value="必ずこのサーバーでお話した上で投票をお願いします。\n複数回投票した場合は、最新のものを反映します。\nこの方の入場について、NG等意見のある方はお問い合わせください。",
            inline=False
        )

        # 転記とボタン表示
        sent_message = await destination_channel.send(embed=embed)
        logger.info(f"メッセージが転記されました: {sent_message.id}")

        # スレッド作成
        thread_parent_channel = bot.get_channel(THREAD_PARENT_CHANNEL_ID)
        if thread_parent_channel:
            thread = await thread_parent_channel.create_thread(name=f"{message.author.display_name}のリアクション投票スレッド", auto_archive_duration=10080)
            user_threads[message.author.id] = thread
            view = create_reaction_view(thread)
            await sent_message.edit(view=view)
            logger.info(f"スレッドが作成されました: {thread.id} for {message.author.display_name}")

    except Exception as e:
        logger.error(f"スレッド作成またはメッセージ転記に失敗しました: {e}")

# Bot再起動後にViewを再アタッチする処理
@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user.name}#{bot.user.discriminator}")
    destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)

    if destination_channel:
        async for message in destination_channel.history(limit=50):
            if message.author == bot.user and message.embeds and message.thread:
                view = create_reaction_view(message.thread)
                await message.edit(view=view)
                logger.info(f"再起動後にViewを再アタッチしました: {message.id}")

# Botの起動
bot.run(TOKEN)
