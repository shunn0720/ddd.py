import os
import discord
from discord.ext import commands
import psycopg2
import random

# DiscordとHerokuの設定
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

DATABASE_URL = os.getenv('DATABASE_URL')  # HerokuのPostgreSQL URL
conn = psycopg2.connect(DATABASE_URL, sslmode='require')
cursor = conn.cursor()

# 各種ID設定
FORUM_CHANNEL_ID = 1288321432828248124  # フォーラムチャンネルID
THREAD_ID = 1288407362318893109         # スレッドID
READ_LATER_REACTION_ID = 1307321645480022046
FAVORITE_REACTION_ID = 1307735348184354846
RANDOM_EXCLUDE_REACTION_ID = 1304763661172346973
READ_LATER_INCLUDE_REACTION_ID = 1306461538659340308

# Embedを作成する関数
def create_embed(result: str = None):
    description = (
        "botがｴﾛ漫画を選んでくれるよ！<a:c296:1288305823323263029>\n\n"
        "🔵：自分の<:b431:1289782471197458495>を除外しない\n"
        "🔴：自分の<:b431:1289782471197458495>を除外する\n\n"
        "**【ランダム】**　：全体から選ぶ\n"
        "**【あとで読む】**：<:b434:1304690617405669376>を付けた投稿から選ぶ\n"
        "**【お気に入り】**：<:b435:1304690627723657267>を付けた投稿から選ぶ"
    )
    if result:
        description += f"\n\n**結果**: {result}"

    return discord.Embed(
        title="おすすめ漫画セレクター",
        description=description,
        color=discord.Color.magenta()
    )

# ボタンを作成する関数
def create_view():
    view = discord.ui.View(timeout=None)  # ボタンのタイムアウトを無効化
    # 上段のボタン（青色）
    top_row_buttons = [
        {"label": "ランダム(通常)", "action": "recommend_manga", "style": discord.ButtonStyle.primary},
        {"label": "あとで読む(通常)", "action": "later_read", "style": discord.ButtonStyle.primary},
        {"label": "お気に入り", "action": "favorite", "style": discord.ButtonStyle.primary}
    ]
    for button in top_row_buttons:
        view.add_item(discord.ui.Button(label=button["label"], custom_id=button["action"], style=button["style"], row=0))

    # 下段のボタン（赤色）
    bottom_row_buttons = [
        {"label": "ランダム", "action": "random_exclude", "style": discord.ButtonStyle.danger},
        {"label": "あとで読む", "action": "read_later_exclude", "style": discord.ButtonStyle.danger}
    ]
    for button in bottom_row_buttons:
        view.add_item(discord.ui.Button(label=button["label"], custom_id=button["action"], style=button["style"], row=1))

    return view

# ボタン付きメッセージを生成
async def create_button_message(channel):
    """
    ボタン付きメッセージを送信し、データベースに保存。
    """
    embed = create_embed()
    view = create_view()
    message = await channel.send(embed=embed, view=view)
    return message

# ボタンのアクションに対応する処理
async def handle_interaction(interaction, action: str):
    # 初期化
    forum_channel = bot.get_channel(FORUM_CHANNEL_ID)
    thread = forum_channel.get_thread(THREAD_ID)
    messages = [message async for message in thread.history(limit=100)]
    result = "条件に合うメッセージが見つかりませんでした。"

    # 各アクションの処理
    if action == "recommend_manga":
        if messages:
            random_message = random.choice(messages)
            result = f"おすすめの漫画はこちら: [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
    elif action == "later_read":
        filtered = [
            msg for msg in messages if any(
                reaction.emoji.id == READ_LATER_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            )
        ]
        if filtered:
            random_message = random.choice(filtered)
            result = f"あとで読む: [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
    elif action == "favorite":
        filtered = [
            msg for msg in messages if any(
                reaction.emoji.id == FAVORITE_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            )
        ]
        if filtered:
            random_message = random.choice(filtered)
            result = f"お気に入り: [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
    elif action == "random_exclude":
        filtered = [
            msg for msg in messages if not any(
                reaction.emoji.id == RANDOM_EXCLUDE_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            )
        ]
        if filtered:
            random_message = random.choice(filtered)
            result = f"ランダム(除外): [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"
    elif action == "read_later_exclude":
        filtered = [
            msg for msg in messages if not any(
                reaction.emoji.id == RANDOM_EXCLUDE_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            ) and any(
                reaction.emoji.id == READ_LATER_INCLUDE_REACTION_ID for reaction in msg.reactions if hasattr(reaction.emoji, 'id')
            )
        ]
        if filtered:
            random_message = random.choice(filtered)
            result = f"あとで読む(除外): [リンク](https://discord.com/channels/{random_message.guild.id}/{random_message.channel.id}/{random_message.id})"

    # メッセージを更新
    updated_embed = create_embed(result)
    await interaction.message.edit(embed=updated_embed, view=create_view())
    await interaction.response.defer()  # 反応を遅らせて処理を継続

# ボタンクリックのインタラクション処理
@bot.event
async def on_interaction(interaction: discord.Interaction):
    try:
        # カスタムIDを取得
        action = interaction.data['custom_id']
        
        # 有効なアクションを実行
        await handle_interaction(interaction, action)
    except KeyError:
        # 未定義のカスタムIDが押された場合
        await interaction.response.send_message("このアクションは現在サポートされていません。", ephemeral=True)
    except discord.errors.NotFound:
        # メッセージが削除されている場合
        await interaction.response.send_message("対象のメッセージが見つかりませんでした。", ephemeral=True)
    except discord.errors.InteractionResponded:
        # 既に応答が完了している場合（予防的な処理）
        print("インタラクションに対するレスポンスが既に送信されています。")
    except Exception as e:
        # その他の予期しないエラーをキャッチ
        print(f"インタラクション処理中にエラーが発生しました: {e}")
        await interaction.response.send_message("エラーが発生しました。管理者に報告してください。", ephemeral=True)

# コマンドでボタン付きメッセージを追加
@bot.command()
async def add_buttons(ctx):
    await create_button_message(ctx.channel)
    await ctx.send("ボタンを作成しました！")

# Botの起動
bot.run(os.getenv('DISCORD_TOKEN'))
