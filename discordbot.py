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
DESTINATION_CHANNEL_ID = 1299231533437292596  # 転記先チャンネルID
THREAD_PARENT_CHANNEL_ID = 1299231693336743996  # スレッドが作成されるチャンネルID

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

# コメントを入力するためのモーダル
class CommentModal(Modal):
    def __init__(self, type):
        super().__init__(title="投票画面", custom_id=str(type))
        self.comment = TextInput(
            label="コメント",
            style=discord.TextStyle.paragraph,
            placeholder="理由がある場合はこちらに入力してください（そのまま送信も可）",
            required=False
        )
        self.add_item(self.comment)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            option = reaction_options[int(interaction.data["custom_id"])]
            thread = user_threads.get(interaction.user.id)

            if thread is None:
                await interaction.response.send_message("スレッドが見つかりませんでした。", ephemeral=True)
                return
            
            embed = discord.Embed(color=option['color']) 
            embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            embed.add_field(
                name="リアクション結果",
                value=f"{interaction.user.display_name} が '{option['label']}' を押しました。",
                inline=False
            )
            embed.add_field(
                name="点数",
                value=f"{option['score']}点",
                inline=False
            )
            embed.add_field(
                name="コメント",
                value=self.comment.value if self.comment.value else "コメントなし",
                inline=False
            )

            # スレッドにメッセージを送信
            await thread.send(embed=embed)
            await interaction.response.send_message(f"投票ありがとう！", ephemeral=True)

        except discord.HTTPException as e:
            logger.error(f"HTTPエラーが発生しました: {str(e)}")
            await interaction.response.send_message(f"HTTPエラーが発生しました: {str(e)}", ephemeral=True)
        except discord.Forbidden:
            logger.error("操作の権限がありません。")
            await interaction.response.send_message("この操作を行う権限がありません。", ephemeral=True)
        except discord.NotFound:
            logger.error("指定されたリソースが見つかりませんでした。")
            await interaction.response.send_message("指定されたリソースが見つかりませんでした。", ephemeral=True)
        except Exception as e:
            logger.error(f"予期しないエラーが発生しました: {str(e)}")
            await interaction.response.send_message(f"エラーが発生しました: {str(e)}", ephemeral=True)

# ボタンを作成するクラス
class ReactionButton(Button):
    def __init__(self, label, color, score, user, custom_id):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.label = label
        self.color = color
        self.score = score
        self.user = user
        self.custom_id = custom_id

# 7日間のタイムアウトを設定してViewにボタンを追加
def create_reaction_view(user):
    view = View(timeout=10080 * 60)  # 7日間のタイムアウト（分単位）
    for option in reaction_options:
        view.add_item(ReactionButton(label=option["label"], color=option["color"], score=option["score"], user=user, custom_id=option["custom_id"]))

    # タイムアウト後の処理：ボタンを無効化して更新
    async def on_timeout():
        for item in view.children:
            item.disabled = True
        await view.message.edit(view=view)  # タイムアウト後にボタンを消す

    view.on_timeout = on_timeout  # タイムアウトイベント設定
    return view

# on_message イベントでメッセージを転記
@bot.event
async def on_message(message):
    if message.channel.id in SOURCE_CHANNEL_IDS and not message.author.bot:
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)

        # メッセージの送信者のEmbedを作成して転記
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

        view = create_reaction_view(message.author)
        sent_message = await destination_channel.send(embed=embed, view=view)
        view.message = sent_message  # viewにメッセージ参照を持たせる

        logger.info(f"メッセージが転記されました: {sent_message.id}")  # ログ出力

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

# Botの起動
bot.run(TOKEN)
