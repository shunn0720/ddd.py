import discord
import asyncio
import os
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True  # メッセージ内容の取得に必要
intents.reactions = True  # リアクションを検知するのに必要
intents.members = True  # メンバー情報の取得に必要

# Herokuの環境変数からトークンを取得
TOKEN = os.getenv('DISCORD_TOKEN')

# チャンネルIDを設定
SOURCE_CHANNEL_IDS = [1282174861996724295, 1282174893290557491]
DESTINATION_CHANNEL_ID = 1289802546180784240  # 新しいIDに変更
THREAD_PARENT_CHANNEL_ID = 1288732448900775958

# コマンド実行を許可するユーザーID
AUTHORIZED_USER_IDS = [822460191118721034, 302778094320615425]

# Bot設定
bot = commands.Bot(command_prefix='!', intents=intents)

# ボタンの選択肢
reaction_options = ["すごくいい人", "いい人", "微妙な人", "やばい人"]

class ReactionButton(discord.ui.Button):
    def __init__(self, label):
        super().__init__(label=label, style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"{interaction.user.display_name} は '{self.label}' を選びました！", ephemeral=True)

# Viewにボタンを追加
def create_reaction_view():
    view = discord.ui.View()
    for option in reaction_options:
        view.add_item(ReactionButton(label=option))
    return view

@bot.event
async def on_message(message):
    if message.channel.id in SOURCE_CHANNEL_IDS and not message.author.bot:
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)

        # Embedメッセージの作成
        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(name=message.author.display_name, icon_url=message.author.avatar.url)

        # 上部に表示する固定メッセージ
        embed.add_field(
            name="🌱つぼみ審査投票フォーム",
            value=(
                "必ずこのｻｰﾊﾞｰでお話した上で投票をお願いします。\n"
                "複数回投票した場合は、最新のものを反映します。\n"
                "この方の入場について、NG等意見のある方はお問い合わせください。"
            ),
            inline=False
        )

        sent_message = await destination_channel.send(embed=embed, view=create_reaction_view())

        # スレッドを作成
        thread_parent_channel = bot.get_channel(THREAD_PARENT_CHANNEL_ID)
        thread = await thread_parent_channel.create_thread(
            name=f"{message.author.display_name}のリアクション投票",
            message=sent_message,
            auto_archive_duration=10080  # 7日
        )

        await schedule_reaction_summary(thread, sent_message)

async def schedule_reaction_summary(thread, message):
    await asyncio.sleep(5 * 24 * 60 * 60)
    await thread.send("5日後のリアクション集計です。")

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
