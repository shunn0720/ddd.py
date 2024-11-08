import discord
import os
import logging
import psycopg2
from discord.ext import commands
from discord.ui import Button, View

# ログの設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Intents設定
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

# Heroku環境変数からトークンとデータベースURLを取得
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# チャンネルIDの設定
SOURCE_CHANNEL_IDS = [1299231408551755838, 1299231612944257036]
DESTINATION_CHANNEL_ID = 1299231533437292596
THREAD_PARENT_CHANNEL_ID = 1299231693336743996

# ボタンの選択肢とスコア
reaction_options = [
    {"label": "入ってほしい！", "color": discord.Color.green(), "score": 2, "custom_id": "type1"},
    {"label": "良い人！", "color": discord.Color.green(), "score": 1, "custom_id": "type2"},
    {"label": "微妙", "color": discord.Color.red(), "score": -1, "custom_id": "type3"},
    {"label": "入ってほしくない", "color": discord.Color.red(), "score": -2, "custom_id": "type4"}
]

def init_db():
    """データベースの初期化"""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        with conn.cursor() as cur:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS user_votes (
                    user_id BIGINT,
                    voter_id BIGINT,
                    reaction_type TEXT NOT NULL,
                    score INT NOT NULL,
                    PRIMARY KEY (user_id, voter_id)
                )
            ''')
        conn.commit()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
    finally:
        if conn:
            conn.close()

def save_vote_data(user_id, voter_id, reaction_type, score):
    """投票データをデータベースに保存（既存の投票は更新）"""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        with conn.cursor() as cur:
            cur.execute('''
                INSERT INTO user_votes (user_id, voter_id, reaction_type, score)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, voter_id) DO UPDATE
                SET reaction_type = EXCLUDED.reaction_type,
                    score = EXCLUDED.score
            ''', (user_id, voter_id, reaction_type, score))
        conn.commit()
        logger.info(f"Vote data saved: user_id={user_id}, voter_id={voter_id}, reaction={reaction_type}")
    except Exception as e:
        logger.error(f"Error saving vote data: {e}")
    finally:
        if conn:
            conn.close()

# Botの設定
bot = commands.Bot(command_prefix='!', intents=intents, reconnect=True)

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user}')
    init_db()

@bot.event
async def on_disconnect():
    # 切断された際の処理
    logger.warning("Bot disconnected. Trying to reconnect...")
    await asyncio.sleep(5)

# ボタン押下時の処理
async def on_button_click(interaction: discord.Interaction):
    custom_id = interaction.data["custom_id"]
    option_index = int(custom_id[-1]) - 1  # 選択肢のインデックス
    option = reaction_options[option_index]
    
    # 投票データの保存（更新または挿入）
    save_vote_data(
        user_id=interaction.message.author.id,       # 対象ユーザーのID
        voter_id=interaction.user.id,                # 投票者のID
        reaction_type=option["label"],               # 投票内容（ラベル）
        score=option["score"]                        # スコア
    )

    # 投票結果をスレッドに送信
    embed = discord.Embed(color=option["color"])
    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    embed.add_field(name="リアクション結果", value=f"{interaction.user.display_name} が '{option['label']}' を押しました。", inline=False)
    embed.add_field(name="点数", value=f"{option['score']}点", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

class ReactionButton(Button):
    def __init__(self, label, color, score, custom_id):
        super().__init__(label=label, style=discord.ButtonStyle.primary if color == discord.Color.green() else discord.ButtonStyle.danger)
        self.custom_id = custom_id

def create_reaction_view():
    view = View(timeout=None)
    for option in reaction_options:
        view.add_item(ReactionButton(label=option["label"], color=option["color"], score=option["score"], custom_id=option["custom_id"]))
    return view

@bot.event
async def on_message(message):
    if message.channel.id in SOURCE_CHANNEL_IDS and not message.author.bot:
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
        if not destination_channel:
            logger.error("転送先チャンネルが見つかりませんでした。")
            return

        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(name=message.author.display_name)
        embed.set_thumbnail(url=message.author.display_avatar.url)
        embed.add_field(
            name="🌱つぼみ審査投票フォーム",
            value="必ずこのサーバーでお話した上で投票をお願いします。\n複数回投票した場合は、最新のものを反映します。\nこの方の入場について、NG等意見のある方はお問い合わせください。",
            inline=False
        )

        view = create_reaction_view()
        try:
            await destination_channel.send(embed=embed, view=view)
        except discord.HTTPException as e:
            logger.error(f"メッセージの送信に失敗しました: {e}")

bot.run(DISCORD_TOKEN)
