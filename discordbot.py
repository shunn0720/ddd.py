import discord
import asyncio
import os
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput

intents = discord.Intents.default()
intents.message_content = True  # メッセージ内容の取得に必要
intents.reactions = True  # リアクションを検知するのに必要
intents.members = True  # メンバー情報の取得に必要

# Herokuの環境変数からトークンを取得
TOKEN = os.getenv('DISCORD_TOKEN')

# チャンネルIDを設定
SOURCE_CHANNEL_IDS = [1282174861996724295, 1282174893290557491, 1288159832809144370]
DESTINATION_CHANNEL_ID = 1297748876735942738  # ここに転記されたユーザー情報が表示
THREAD_PARENT_CHANNEL_ID = 1288732448900775958  # ここにスレッドを作成

# コマンド実行を許可するユーザーID
AUTHORIZED_USER_IDS = [822460191118721034, 302778094320615425]

# ボタンの選択肢とスコア
reaction_options = [
    {"label": "入ってほしい！", "color": discord.Color.green(), "score": 2},
    {"label": "良い人！", "color": discord.Color.green(), "score": 1},
    {"label": "微妙", "color": discord.Color.red(), "score": -1},
    {"label": "入ってほしくない", "color": discord.Color.red(), "score": -2}
]

# ボタンを押したユーザーのスレッドを追跡する辞書
user_threads = {}

# Bot設定
bot = commands.Bot(command_prefix='!', intents=intents)

# コメントを入力するためのモーダル
class CommentModal(Modal):
    def __init__(self, label, color, score, user, interaction):
        # モーダルのタイトルを「投票画面」に変更
        super().__init__(title="投票画面")

        self.label = label
        self.color = color
        self.score = score
        self.user = user

        # コメント入力フィールドのプレースホルダーを変更し、必須ではなくする
        self.comment = TextInput(
            label="コメント",
            style=discord.TextStyle.paragraph,
            placeholder="理由がある場合はこちらに入力してください（そのまま送信も可）",
            required=False  # 入力を必須にしない
        )
        self.add_item(self.comment)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            print(f"{interaction.user.display_name} が '{self.label}' ボタンを押し、コメントを送信しました。")
            # 既存のスレッドを取得
            thread = user_threads.get(self.user.id)

            if thread is None:
                print(f"スレッドが見つかりません: {self.user.display_name}")
                await interaction.response.send_message("スレッドが見つかりませんでした。", ephemeral=True)
                return

            # ボタンを押したユーザー情報とコメントをEmbedでスレッドに転記
            embed = discord.Embed(color=self.color)

            # ボタンを押したユーザーの名前とアイコンを表示
            embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)

            embed.add_field(
                name="リアクション結果",
                value=f"{interaction.user.display_name} が '{self.label}' を押しました。",
                inline=False
            )
            embed.add_field(
                name="点数",
                value=f"{self.score}点",  # ここで点数を追加
                inline=False
            )
            embed.add_field(
                name="コメント",
                value=self.comment.value if self.comment.value else "コメントなし",
                inline=False
            )

            # スレッドにメッセージを送信
            await thread.send(embed=embed)
            print(f"スレッドにコメントが転記されました: {interaction.user.display_name}")

            # 応答メッセージを送信
            await interaction.response.send_message(f"投票ありがとなっつ！", ephemeral=True)

        except Exception as e:
            print(f"エラーが発生しました: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("インタラクションに失敗しました。もう一度お試しください。", ephemeral=True)
            else:
                await interaction.followup.send("インタラクションに失敗しました。もう一度お試しください。", ephemeral=True)

# ボタンをクリックしたときの処理
class ReactionButton(Button):
    def __init__(self, label, color, score, user):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.label = label
        self.color = color
        self.score = score
        self.user = user

    async def callback(self, interaction: discord.Interaction):
        try:
            print(f"{interaction.user.display_name} が '{self.label}' ボタンを押しました。")
            modal = CommentModal(label=self.label, color=self.color, score=self.score, user=self.user, interaction=interaction)
            await interaction.response.send_modal(modal)

        except Exception as e:
            print(f"エラーが発生しました: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("インタラクションに失敗しました。もう一度お試しください。", ephemeral=True)
            else:
                await interaction.followup.send("インタラクションに失敗しました。もう一度お試しください。", ephemeral=True)

# Viewにボタンを追加
def create_reaction_view(user, message_id):
    view = View(timeout=None)  # タイムアウトを無効にする
    for option in reaction_options:
        view.add_item(ReactionButton(label=option["label"], color=option["color"], score=option["score"], user=user))
    return view

# on_message イベントでメッセージを転記
@bot.event
async def on_message(message):
    if message.channel.id in SOURCE_CHANNEL_IDS and not message.author.bot:
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)

        # メッセージの送信者のEmbedを作成して転記
        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.add_field(
            name="🌱つぼみ審査投票フォーム",
            value=(
                "必ずこのサーバーでお話した上で投票をお願いします。\n"
                "複数回投票した場合は、最新のものを反映します。\n"
                "この方の入場について、NG等意見のある方はお問い合わせください。"
            ),
            inline=False
        )

        # ユーザーのアイコンを大きくEmbedに表示
        embed.set_image(url=message.author.display_avatar.url)

        sent_message = await destination_channel.send(embed=embed)
        print(f"メッセージが転記されました: {sent_message.id}")  # デバッグ用ログ

        # Viewを作成してメッセージに追加
        view = create_reaction_view(message.author, sent_message.id)
        await sent_message.edit(view=view)

        # スレッド作成
        thread_parent_channel = bot.get_channel(THREAD_PARENT_CHANNEL_ID)
        try:
            thread = await thread_parent_channel.create_thread(
                name=f"{message.author.display_name}のリアクション投票スレッド",
                auto_archive_duration=10080  # 7日
            )
            user_threads[message.author.id] = thread
            print(f"スレッドが作成されました: {thread.id} for {message.author.display_name}")  # デバッグ用ログ
        except Exception as e:
            print(f"スレッド作成に失敗しました: {e}")

# Bot再起動後にViewを再アタッチする処理
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
