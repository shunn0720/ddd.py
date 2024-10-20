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
SOURCE_CHANNEL_IDS = [1282174861996724295, 1282174893290557491]
DESTINATION_CHANNEL_ID = 1289802546180784240  # ここに転記されたユーザー情報が表示
THREAD_PARENT_CHANNEL_ID = 1288732448900775958  # ここにスレッドを作成

# コマンド実行を許可するユーザーID
AUTHORIZED_USER_IDS = [822460191118721034, 302778094320615425]

# ボタンの選択肢
reaction_options = ["入ってほしい！", "良い人", "微妙", "入ってほしくない"]

# ボタンを押したユーザーのスレッドを追跡する辞書
user_threads = {}

# Bot設定
bot = commands.Bot(command_prefix='!', intents=intents)

# ユーザー情報を転記するembedを作成
def create_user_embed(user: discord.Member):
    embed = discord.Embed(color=discord.Color.blue())
    embed.set_author(name=user.display_name, icon_url=user.avatar.url)
    embed.add_field(
        name="🌱つぼみ審査投票フォーム",
        value=(
            "必ずこのｻｰﾊｰでお話した上で投票をお願いします。\n"
            "複数回投票した場合は、最新のものを反映します。\n"
            "この方の入場について、NG等意見のある方はお問い合わせください。"
        ),
        inline=False
    )
    return embed

# コメントを入力するためのモーダル
class CommentModal(Modal):
    def __init__(self, label, user, interaction):
        super().__init__(title="コメントを入力してください")

        self.label = label
        self.user = user
        self.interaction = interaction

        # コメント入力フィールドを追加
        self.comment = TextInput(
            label="コメント",
            style=discord.TextStyle.paragraph,
            placeholder="コメントを入力してください",
            required=True
        )
        self.add_item(self.comment)

    async def on_submit(self, interaction: discord.Interaction):
        print(f"{interaction.user.display_name} が '{self.label}' ボタンを押し、コメントを送信しました。")
        # 既存のスレッドを取得
        thread = user_threads.get(self.user.id)

        if thread is None:
            print(f"スレッドが見つかりません: {self.user.display_name}")
            await interaction.response.send_message("スレッドが見つかりませんでした。", ephemeral=True)
            return

        # ボタンを押したユーザー情報とコメントをEmbedでスレッドに転記
        embed = discord.Embed(color=discord.Color.green())
        embed.set_author(name=self.user.display_name, icon_url=self.user.avatar.url)
        embed.add_field(
            name="リアクション結果",
            value=f"{interaction.user.display_name} が '{self.label}' を押しました。",
            inline=False
        )
        embed.add_field(
            name="コメント",
            value=self.comment.value,  # 入力されたコメントをここに表示
            inline=False
        )

        # スレッドにメッセージを送信
        await thread.send(embed=embed)
        print(f"スレッドにコメントが転記されました: {self.user.display_name}")
        await interaction.response.send_message(f"あなたのコメントがスレッドに転記されました！", ephemeral=True)

# ボタンをクリックしたときの処理
class ReactionButton(Button):
    def __init__(self, label, user):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.user = user

    async def callback(self, interaction: discord.Interaction):
        print(f"{interaction.user.display_name} が '{self.label}' ボタンを押しました。")
        
        # コメントを入力するためのモーダルを表示
        modal = CommentModal(label=self.label, user=self.user, interaction=interaction)
        await interaction.response.send_modal(modal)  # モーダルを表示して応答

# Viewにボタンを追加
def create_reaction_view(user):
    view = View()
    for option in reaction_options:
        view.add_item(ReactionButton(label=option, user=user))
    return view

# on_message イベントでメッセージを転記
@bot.event
async def on_message(message):
    if message.channel.id in SOURCE_CHANNEL_IDS and not message.author.bot:
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)

        # ユーザー情報のEmbedを作成して転記
        embed = create_user_embed(message.author)
        sent_message = await destination_channel.send(embed=embed, view=create_reaction_view(message.author))
        print(f"メッセージが転記されました: {sent_message.id}")  # デバッグ用ログ

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

# メッセージを削除するコマンド
@bot.command()
async def 終了(ctx, message_id: int):
    if ctx.author.id not in AUTHORIZED_USER_IDS:
        await ctx.send("このコマンドを実行する権限がありません。")
        return

    try:
        channel = bot.get_channel(DESTINATION_CHANNEL_ID)
        message = await channel.fetch_message(message_id)
        await message.delete()
        await ctx.send(f"メッセージID {message_id} を削除しました。")

    except discord.NotFound:
        await ctx.send("指定されたメッセージが見つかりません。")
    except discord.Forbidden:
        await ctx.send("このメッセージを削除する権限がありません。")
    except discord.HTTPException as e:
        await ctx.send(f"メッセージの削除に失敗しました: {str(e)}")

# Botの起動
bot.run(TOKEN)
