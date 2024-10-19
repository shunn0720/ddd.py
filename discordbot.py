import discord
import asyncio
import os  # 環境変数を読み込むために必要
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

# ボタンの選択肢 (文字列は保持するが、絵文字としてリアクションを追加)
reaction_options = {
    "すごくいい人": "👍",  # 文字列を絵文字に対応
    "いい人": "😊",
    "微妙な人": "😐",
    "やばい人": "👎"
}

# Bot設定
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_message(message):
    if message.channel.id in SOURCE_CHANNEL_IDS and not message.author.bot:
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)

        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(name=message.author.display_name, icon_url=message.author.avatar.url)

        sent_message = await destination_channel.send(embed=embed)

        # reaction_options の対応する絵文字をリアクションとして追加
        for option in reaction_options.values():
            await sent_message.add_reaction(option)

        thread_parent_channel = bot.get_channel(THREAD_PARENT_CHANNEL_ID)
        thread = await thread_parent_channel.create_thread(
            name=f"{message.author.display_name}のリアクション投票",
            message=sent_message,
            auto_archive_duration=10080  # 7日
        )

        await schedule_reaction_summary(thread, sent_message)

async def schedule_reaction_summary(thread, message):
    await asyncio.sleep(5 * 24 * 60 * 60)

    reaction_summary = []
    for reaction in message.reactions:
        users = await reaction.users().flatten()
        user_names = [user.display_name for user in users if not user.bot]
        if user_names:
            reaction_summary.append(f"{reaction.emoji}: {', '.join(user_names)}")

    if reaction_summary:
        summary_message = "\n".join(reaction_summary)
        await thread.send(f"5日後のリアクション結果:\n{summary_message}")
    else:
        await thread.send("5日後のリアクション結果: 誰もリアクションを押しませんでした。")

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

# "deldel" コマンドを定義（指定されたメッセージを削除）
@bot.command()
async def deldel(ctx, message_id: int):
    if ctx.author.id not in AUTHORIZED_USER_IDS:
        await ctx.send("このコマンドを実行する権限がありません。")
        return

    try:
        # チャンネルから指定されたメッセージを取得
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
