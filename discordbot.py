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

# トークンまたはデータベースURLが設定されていない場合はエラー
if not TOKEN or not DATABASE_URL:
    logger.critical("DISCORD_TOKENまたはDATABASE_URLが環境変数に設定されていません。")
    exit(1)

# チャンネルIDを設定
SOURCE_CHANNEL_IDS = [1282174861996724295, 1282174893290557491, 1288159832809144370]
DESTINATION_CHANNEL_ID = 1297748876735942738  # ここに転記されたユーザー情報が表示
THREAD_PARENT_CHANNEL_ID = 1288732448900775958  # ここにスレッドを作成

# ボタンの選択肢とスコア
reaction_options = [
    {"label": "入ってほしい！", "style": discord.ButtonStyle.success, "score": 2, "custom_id": "type1"},
    {"label": "良い人！", "style": discord.ButtonStyle.success, "score": 1, "custom_id": "type2"},
    {"label": "微妙", "style": discord.ButtonStyle.danger, "score": -1, "custom_id": "type3"},
    {"label": "入ってほしくない", "style": discord.ButtonStyle.danger, "score": -2, "custom_id": "type4"}
]

# Bot設定
bot = commands.Bot(command_prefix='!', intents=intents)

def get_db_connection(retries=3, delay=2):
    """データベース接続を確立する。接続に失敗した場合、指定回数リトライを試みる。"""
    attempt = 0
    while attempt < retries:
        try:
            connection = psycopg2.connect(DATABASE_URL, sslmode='require')
            logger.info("データベース接続に成功しました")
            return connection
        except psycopg2.OperationalError as e:
            logger.warning(f"データベース接続に失敗しました (試行 {attempt + 1}/{retries})。エラー: {e}")
            attempt += 1
            if attempt < retries:
                logger.info(f"{delay}秒後に再試行します...")
                time.sleep(delay)
        except Exception as e:
            logger.error(f"予期しないエラーが発生しました: {e}")
            break

    logger.critical("データベース接続に失敗しました。指定された回数のリトライを試みましたが、接続できませんでした。")
    raise RuntimeError("データベース接続を確立できませんでした。")

# コメントを入力するためのモーダル
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
        # 重複投票を防ぐため、以前のメッセージを削除
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

        message = await self.thread.send(embed=embed)
        await interaction.response.send_message("投票を完了しました！", ephemeral=True)

        # 投票データをデータベースに保存
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO user_votes (user_id, thread_id, message_id)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (user_id, thread_id)
                        DO UPDATE SET message_id = EXCLUDED.message_id
                    """, (interaction.user.id, self.thread.id, message.id))
                conn.commit()
            except Exception as e:
                logger.error(f"データベースへの投票保存エラー: {e}")
            finally:
                conn.close()

# ボタンを作成するクラス
class ReactionButton(Button):
    def __init__(self, label, style, score, reaction_type, thread, user):
        super().__init__(label=label, style=style, custom_id=str(reaction_type))
        self.reaction_type = reaction_type
        self.thread = thread
        self.user = user

    async def callback(self, interaction: discord.Interaction):
        # カスタムIDが無効な場合の対策
        if not self.custom_id.isdigit() or int(self.custom_id) >= len(reaction_options):
            await interaction.response.send_message("無効な操作が検出されました。", ephemeral=True)
            return

        # データベースからユーザーの前回のメッセージIDを取得
        conn = get_db_connection()
        previous_message_id = None
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT message_id FROM user_votes WHERE user_id = %s AND thread_id = %s", (interaction.user.id, self.thread.id))
                    result = cur.fetchone()
                    if result:
                        previous_message_id = result[0]
            except Exception as e:
                logger.error(f"データベースからのメッセージID取得エラー: {e}")
            finally:
                conn.close()

        modal = CommentModal(self.reaction_type, self.thread, previous_message_id)
        await interaction.response.send_modal(modal)

# Viewにボタンを追加
def create_reaction_view(thread, user):
    view = View(timeout=None)  # ボタンが消えないようにtimeoutをNoneに設定
    for i, option in enumerate(reaction_options):
        view.add_item(ReactionButton(label=option["label"], style=option["style"], score=option["score"], reaction_type=i, thread=thread, user=user))
    return view

# on_message イベントでメッセージを転記
@bot.event
async def on_message(message):
    if message.channel.id in SOURCE_CHANNEL_IDS and not message.author.bot:
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)

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

        sent_message = await destination_channel.send(embed=embed)
        thread = await sent_message.create_thread(name=f"{message.author.display_name}のリアクション投票スレッド")
        logger.info(f"スレッドが作成されました: {thread.id} for {message.author.display_name}")

        view = create_reaction_view(thread, message.author)
        await sent_message.edit(view=view)

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user}')
    destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
    async for message in destination_channel.history(limit=20):
        if message.author == bot.user and message.thread:
            try:
                view = create_reaction_view(message.thread, message.author)
                await message.edit(view=view)
                logger.info(f"再起動後にViewを再アタッチしました: {message.id}")
            except Exception as e:
                logger.error(f"View再アタッチに失敗しました: {e}")

bot.run(TOKEN)
