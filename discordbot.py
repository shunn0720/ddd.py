import discord
import os
import logging
import psycopg2
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
import time

# ログの設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Discord intentsの設定
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

# 環境変数からトークンとデータベースURLを取得
TOKEN = os.getenv('DISCORD_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# チャンネルID設定
SOURCE_CHANNEL_IDS = [1282174861996724295, 1282174893290557491, 1288159832809144370]
DESTINATION_CHANNEL_ID = 1297748876735942738
THREAD_PARENT_CHANNEL_ID = 1288732448900775958

# ボタンの設定
reaction_options = [
    {"label": "入ってほしい！", "style": discord.ButtonStyle.success, "score": 2, "custom_id": "type1"},
    {"label": "良い人！", "style": discord.ButtonStyle.success, "score": 1, "custom_id": "type2"},
    {"label": "微妙", "style": discord.ButtonStyle.danger, "score": -1, "custom_id": "type3"},
    {"label": "入ってほしくない", "style": discord.ButtonStyle.danger, "score": -2, "custom_id": "type4"}
]

# Bot設定
bot = commands.Bot(command_prefix='!', intents=intents)

def get_db_connection():
    try:
        connection = psycopg2.connect(DATABASE_URL, sslmode='require')
        logger.info("データベース接続に成功しました")
        return connection
    except Exception as e:
        logger.critical(f"データベース接続に失敗しました: {e}")
        raise

# モーダルの設定
class CommentModal(Modal):
    def __init__(self, reaction_type, thread, previous_message_id):
        super().__init__(title="投票画面")
        self.reaction_type = reaction_type
        self.thread = thread
        self.previous_message_id = previous_message_id

        self.comment = TextInput(
            label="コメント",
            style=discord.TextStyle.paragraph,
            placeholder="理由がある場合はこちらに入力してください（そのまま送信も可）",
            required=False
        )
        self.add_item(self.comment)

    async def on_submit(self, interaction: discord.Interaction):
        if self.previous_message_id:
            try:
                previous_message = await self.thread.fetch_message(self.previous_message_id)
                await previous_message.delete()
            except Exception as e:
                logger.warning(f"前回の投票メッセージの削除に失敗しました: {e}")

        option = reaction_options[self.reaction_type]
        embed = discord.Embed(color=option['style'].value)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="リアクション結果", value=f"{interaction.user.display_name} が '{option['label']}' を押しました。", inline=False)
        embed.add_field(name="点数", value=f"{option['score']}点", inline=False)
        embed.add_field(name="コメント", value=self.comment.value if self.comment.value else "コメントなし", inline=False)

        try:
            new_message = await self.thread.send(embed=embed)
            await interaction.response.send_message("投票を完了しました！", ephemeral=True)
            # 新しい投票メッセージのIDをデータベースに保存
            save_vote(interaction.user.id, new_message.id)
        except Exception as e:
            logger.error(f"投票メッセージの送信に失敗しました: {e}")
            await interaction.response.send_message("投票に失敗しました。", ephemeral=True)

# ボタン設定クラス
class ReactionButton(Button):
    def __init__(self, label, style, score, reaction_type, thread, previous_message_id):
        super().__init__(label=label, style=style, custom_id=f"btn_{reaction_type}")
        self.reaction_type = reaction_type
        self.thread = thread
        self.previous_message_id = previous_message_id

    async def callback(self, interaction: discord.Interaction):
        modal = CommentModal(self.reaction_type, self.thread, self.previous_message_id)
        await interaction.response.send_modal(modal)

# View作成
def create_reaction_view(thread, previous_message_id):
    view = View(timeout=None)  # ボタンが消えないように設定
    for i, option in enumerate(reaction_options):
        view.add_item(ReactionButton(label=option["label"], style=option["style"], score=option["score"], reaction_type=i, thread=thread, previous_message_id=previous_message_id))
    return view

# 投票情報をデータベースに保存
def save_vote(user_id, message_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_votes (user_id, message_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET message_id = EXCLUDED.message_id
            """, (user_id, message_id))
            conn.commit()
            logger.info(f"投票データを保存しました: user_id={user_id}, message_id={message_id}")
    except Exception as e:
        logger.error(f"投票データの保存に失敗しました: {e}")
    finally:
        conn.close()

# メッセージ転記とスレッド作成
@bot.event
async def on_message(message):
    if message.author.bot or message.channel.id not in SOURCE_CHANNEL_IDS:
        return

    destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
    if not destination_channel:
        logger.error("転記先チャンネルが見つかりません。")
        return

    try:
        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
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

        sent_message = await destination_channel.send(embed=embed)
        logger.info(f"メッセージが転記されました: {sent_message.id}")

        thread = await sent_message.create_thread(name=f"{message.author.display_name}のリアクション投票スレッド")
        view = create_reaction_view(thread, None)
        await sent_message.edit(view=view)

        logger.info(f"スレッドが作成されました: {thread.id} for {message.author.display_name}")
    except Exception as e:
        logger.error(f"転記またはスレッド作成に失敗しました: {e}")

# Bot起動
@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user.name}#{bot.user.discriminator}")

bot.run(TOKEN)
