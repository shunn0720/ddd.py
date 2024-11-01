import discord
import os
import logging
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput

# ログの設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Discordの意図設定
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

# Herokuの環境変数からトークンを取得
TOKEN = os.getenv('DISCORD_TOKEN')

# チャンネルIDの設定
SOURCE_CHANNEL_IDS = [1299231408551755838, 1299231612944257036]  # ソースチャンネル
DESTINATION_CHANNEL_ID = 1299231533437292596  # 転記先チャンネル
THREAD_PARENT_CHANNEL_ID = 1299231693336743996  # スレッドの親チャンネル

# コマンド実行を許可するユーザーID
AUTHORIZED_USER_IDS = [822460191118721034, 302778094320615425]

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

# ボタンのコメントを入力するためのモーダル
class CommentModal(Modal):
    def __init__(self, label, color, user, interaction):
        super().__init__(title="投票画面")
        self.label = label
        self.color = color
        self.user = user

        # コメント入力フィールド
        self.comment = TextInput(
            label="コメント",
            style=discord.TextStyle.paragraph,
            placeholder="理由がある場合はこちらに入力してください（そのまま送信も可）",
            required=False
        )
        self.add_item(self.comment)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            thread = user_threads.get(self.user.id)
            if thread is None:
                await interaction.response.send_message("スレッドが見つかりませんでした。", ephemeral=True)
                return

            # 同一ユーザーの過去のリアクションを削除
            async for message in thread.history():
                if message.author == bot.user:
                    if self.label in message.embeds[0].to_dict().get('fields', [])[0].get('value', ''):
                        await message.delete()

            # Embedメッセージ作成
            embed = discord.Embed(color=self.color)
            embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            embed.add_field(
                name="リアクション結果",
                value=f"{interaction.user.display_name} が '{self.label}' を押しました。",
                inline=False
            )
            embed.add_field(
                name="点数",
                value=f"{reaction_options[int(interaction.data['custom_id'][-1]) - 1]['score']}点",
                inline=False
            )
            embed.add_field(
                name="コメント",
                value=self.comment.value if self.comment.value else "コメントなし",
                inline=False
            )
            await thread.send(embed=embed)
            await interaction.response.send_message("投票ありがとう！", ephemeral=True)

        except Exception as e:
            logger.error(f"エラーが発生しました: {str(e)}")
            await interaction.response.send_message(f"エラーが発生しました: {str(e)}", ephemeral=True)

# ボタンをクリックしたときの処理
class ReactionButton(Button):
    def __init__(self, label, color, user):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.label = label
        self.color = color
        self.user = user

    async def callback(self, interaction: discord.Interaction):
        try:
            modal = CommentModal(label=self.label, color=self.color, user=self.user, interaction=interaction)
            await interaction.response.send_modal(modal)

        except Exception as e:
            logger.error(f"エラーが発生しました: {str(e)}")
            await interaction.response.send_message("エラーが発生しました。", ephemeral=True)

# Viewにボタンを追加
def create_reaction_view(user):
    view = View(timeout=10080 * 60)  # 7日後にタイムアウト
    for option in reaction_options:
        view.add_item(ReactionButton(label=option["label"], color=option["color"], user=user))
    return view

# on_message イベントでメッセージを転記
@bot.event
async def on_message(message):
    if message.channel.id in SOURCE_CHANNEL_IDS and not message.author.bot:
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
        
        # Embed作成
        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(name=message.author.display_name)
        embed.set_thumbnail(url=message.author.display_avatar.url)  # 右側に大きくアイコンを表示

        embed.add_field(
            name="🌱つぼみ審査投票フォーム",
            value=(
                "必ずこのサーバーでお話した上で投票をお願いします。\n"
                "複数回投票した場合は、最新のものを反映します。\n"
                "この方の入場について、NG等意見のある方はお問い合わせください。"
            ),
            inline=False
        )

        view = create_reaction_view(message.author)
        sent_message = await destination_channel.send(embed=embed, view=view)
        logger.info(f"メッセージが転記されました: {sent_message.id}")

        # スレッド作成
        thread_parent_channel = bot.get_channel(THREAD_PARENT_CHANNEL_ID)
        try:
            thread = await thread_parent_channel.create_thread(
                name=f"{message.author.display_name}のリアクション投票スレッド",
                auto_archive_duration=10080  # 7日
            )
            user_threads[message.author.id] = thread
            logger.info(f"スレッドが作成されました: {thread.id} for {message.author.display_name}")
        except Exception as e:
            logger.error(f"スレッド作成に失敗しました: {e}")

# Bot再起動後にViewを再アタッチ
@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user}')
    destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
    async for message in destination_channel.history(limit=20):
        if message.author == bot.user and message.embeds:
            try:
                user_id = int(message.embeds[0].thumbnail.url.split("/")[4])
                author = await bot.fetch_user(user_id)
                if author:
                    view = create_reaction_view(author)
                    await message.edit(view=view)
                    logger.info(f"再起動後にViewを再アタッチしました: {message.id}")
            except Exception as e:
                logger.error(f"再アタッチに失敗しました: {e}")

# Botの起動
bot.run(TOKEN)
