import os
import discord
from discord.ext import commands
import random

# Discord Botの設定
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# フォーラムチャンネルやスレッドのID（環境変数などで管理するのが理想）
FORUM_CHANNEL_ID = 1288321432828248124
THREAD_ID = 1288407362318893109
READ_LATER_REACTION_ID = 1307321645480022046
FAVORITE_REACTION_ID = 1307735348184354846
RANDOM_EXCLUDE_REACTION_ID = 1304763661172346973
READ_LATER_INCLUDE_REACTION_ID = 1306461538659340308

# 共通の処理を関数化
async def get_recommendation(action: str):
    """
    各アクションに応じて投稿を選びます。
    """
    forum_channel = bot.get_channel(FORUM_CHANNEL_ID)
    if forum_channel is None:
        return "フォーラムチャンネルが見つかりません。"

    thread = forum_channel.get_thread(THREAD_ID)
    if thread is None:
        return "スレッドが見つかりません。"

    messages = [message async for message in thread.history(limit=100)]
    if not messages:
        return "スレッド内に投稿が見つかりません。"

    # 各ボタンの処理
    if action == "recommend_manga":
        random_message = random.choice(messages)
        return f"おすすめの漫画はこちら: [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
    elif action == "later_read":
        filtered = [
            msg for msg in messages if any(
                reaction.emoji.id == READ_LATER_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            )
        ]
        if not filtered:
            return "条件に合う投稿が見つかりません。"
        random_message = random.choice(filtered)
        return f"あとで読む: [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
    elif action == "favorite":
        filtered = [
            msg for msg in messages if any(
                reaction.emoji.id == FAVORITE_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            )
        ]
        if not filtered:
            return "お気に入りの投稿が見つかりません。"
        random_message = random.choice(filtered)
        return f"お気に入り: [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
    elif action == "random_exclude":
        filtered = [
            msg for msg in messages if not any(
                reaction.emoji.id == RANDOM_EXCLUDE_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            )
        ]
        if not filtered:
            return "条件に合う投稿が見つかりません。"
        random_message = random.choice(filtered)
        return f"ランダム(除外): [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
    elif action == "read_later_exclude":
        filtered = [
            msg for msg in messages if not any(
                reaction.emoji.id == RANDOM_EXCLUDE_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            ) and any(
                reaction.emoji.id == READ_LATER_INCLUDE_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            )
        ]
        if not filtered:
            return "条件に合う投稿が見つかりません。"
        random_message = random.choice(filtered)
        return f"あとで読む(除外): [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
    else:
        return "不明なアクションです。"

# ボタンの表示を設定
def create_view():
    """
    ボタンビューを作成します。
    """
    view = discord.ui.View(timeout=None)
    
    # ボタンを追加
    buttons = [
        {"label": "ランダム(通常)", "action": "recommend_manga", "style": discord.ButtonStyle.primary},
        {"label": "あとで読む(通常)", "action": "later_read", "style": discord.ButtonStyle.primary},
        {"label": "お気に入り", "action": "favorite", "style": discord.ButtonStyle.primary},
        {"label": "ランダム", "action": "random_exclude", "style": discord.ButtonStyle.danger},
        {"label": "あとで読む", "action": "read_later_exclude", "style": discord.ButtonStyle.danger}
    ]

    for idx, button in enumerate(buttons):
        row = 0 if idx < 3 else 1  # 上段と下段に分ける
        view.add_item(discord.ui.Button(label=button["label"], custom_id=button["action"], style=button["style"], row=row))

    return view

# ボタンのクリック処理
@bot.event
async def on_interaction(interaction: discord.Interaction):
    """
    ボタンが押されたときの処理。
    """
    try:
        action = interaction.data["custom_id"]
        result = await get_recommendation(action)
        embed = discord.Embed(title="おすすめ漫画セレクター", description=result, color=discord.Color.magenta())
        await interaction.response.edit_message(embed=embed, view=create_view())
    except Exception as e:
        await interaction.response.send_message(f"エラーが発生しました: {e}", ephemeral=True)

# 初回起動時にボタン付きメッセージを送信
@bot.command()
async def add(ctx):
    embed = discord.Embed(
        title="おすすめ漫画セレクター",
        description=(
            "botがｴﾛ漫画を選んでくれるよ！<a:c296:1288305823323263029>\n\n"
            "🔵：自分の<:b431:1289782471197458495>を除外しない\n"
            "🔴：自分の<:b431:1289782471197458495>を除外する\n\n"
            "**【ランダム】**　：全体から選ぶ\n"
            "**【あとで読む】**：<:b434:1304690617405669376>を付けた投稿から選ぶ\n"
            "**【お気に入り】**：<:b435:1304690627723657267>を付けた投稿から選ぶ"
        ),
        color=discord.Color.magenta()
    )
    view = create_view()
    await ctx.send(embed=embed, view=view)

# Botを起動
bot.run(os.getenv("DISCORD_TOKEN"))
