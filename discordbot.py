import os
import discord
from discord.ext import commands
import random
from asyncio import sleep

# Discord Botの設定
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
FORUM_CHANNEL_ID = 1288321432828248124
THREAD_ID = 1288407362318893109

# 再接続の間隔（秒単位で調整可能）
RECONNECT_WAIT_TIME = 5  

# リアクションID
READ_LATER_REACTION_ID = 1307321645480022046
FAVORITE_REACTION_ID = 1307735348184354846
RANDOM_EXCLUDE_REACTION_ID = 1304763661172346973
READ_LATER_INCLUDE_REACTION_ID = 1306461538659340308

# ロールID
ALLOWED_ROLES = {1283962068197965897, 1246804322969456772}

# 再接続時の待機時間
@bot.event
async def on_disconnect():
    print("Botが切断されました。再接続を試みます...")
    await sleep(RECONNECT_WAIT_TIME)

# 共通処理
async def get_recommendation(action: str):
    forum_channel = bot.get_channel(FORUM_CHANNEL_ID)
    if forum_channel is None:
        return "フォーラムチャンネルが見つかりません。"

    thread = forum_channel.get_thread(THREAD_ID)
    if thread is None:
        return "スレッドが見つかりません。"

    messages = [message async for message in thread.history(limit=100)]
    if not messages:
        return "スレッド内に投稿が見つかりません。"

    if action == "recommend_manga":
        random_message = random.choice(messages)
        return f"おすすめの漫画はこちら: [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
    # 他のアクションも同様に処理...

def create_view():
    view = discord.ui.View(timeout=None)
    buttons = [
        {"label": "ランダム(通常)", "action": "recommend_manga", "style": discord.ButtonStyle.primary},
        # 他のボタンも追加...
    ]
    for idx, button in enumerate(buttons):
        row = 0 if idx < 3 else 1
        view.add_item(discord.ui.Button(label=button["label"], custom_id=button["action"], style=button["style"], row=row))
    return view

@bot.event
async def on_interaction(interaction: discord.Interaction):
    try:
        action = interaction.data["custom_id"]
        result = await get_recommendation(action)
        embed = discord.Embed(title="おすすめ漫画セレクター", description=result, color=discord.Color.magenta())
        await interaction.response.edit_message(embed=embed, view=create_view())
    except Exception as e:
        print(f"エラー: {e}")
        await interaction.response.send_message(f"エラーが発生しました: {e}", ephemeral=True)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.content == "パネル作成":
        if any(role.id in ALLOWED_ROLES for role in message.author.roles):
            embed = discord.Embed(
                title="おすすめ漫画セレクター",
                description="...",
                color=discord.Color.magenta()
            )
            view = create_view()
            await message.channel.send(embed=embed, view=view)
        else:
            await message.channel.send("権限がありません。")
    await bot.process_commands(message)

if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("DISCORD_TOKENが設定されていません。")
