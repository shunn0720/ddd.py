import discord
import os
import logging
import psycopg2
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput

# ログの設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Discordの意図設定
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

# Herokuの環境変数からトークンとデータベースURLを取得
TOKEN = os.getenv('DISCORD_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# チャンネルID設定
SOURCE_CHANNEL_IDS = [1299231408551755838, 1299231612944257036]
DESTINATION_CHANNEL_ID = 1299231533437292596
THREAD_PARENT_CHANNEL_ID = 1299231693336743996

# データベース接続
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

# テーブル作成
def create_table():
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS thread_data (
                    target_user_id BIGINT PRIMARY KEY,
                    thread_id BIGINT NOT NULL
                )
            """)
        conn.commit()

# スレッドデータの保存
def save_thread_data(target_user_id, thread_id):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO thread_data (target_user_id, thread_id)
                VALUES (%s, %s)
                ON CONFLICT (target_user_id) DO UPDATE
                SET thread_id = EXCLUDED.thread_id
            """, (target_user_id, thread_id))
        conn.commit()

# スレッドデータの読み込み
def load_thread_data(target_user_id):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT thread_id FROM thread_data WHERE target_user_id = %s", (target_user_id,))
            result = cursor.fetchone()
            return result[0] if result else None

# ボット設定
bot = commands.Bot(command_prefix='!', intents=intents)

# ボタンの選択肢とスコア
reaction_options = [
    {"label": "入ってほしい！", "color": discord.Color.green(), "score": 2, "custom_id": "type1"},
    {"label": "良い人！", "color": discord.Color.green(), "score": 1, "custom_id": "type2"},
    {"label": "微妙", "color": discord.Color.red(), "score": -1, "custom_id": "type3"},
    {"label": "入ってほしくない", "color": discord.Color.red(), "score": -2, "custom_id": "type4"}
]

# コメントを入力するためのモーダル
class CommentModal(Modal):
    def __init__(self, option, thread):
        super().__init__(title="投票画面")
        self.option = option
        self.thread = thread
        self.comment = TextInput(
            label="コメント",
            style=discord.TextStyle.paragraph,
            placeholder="理由がある場合はこちらに入力してください（そのまま送信も可）",
            required=False
        )
        self.add_item(self.comment)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            embed = discord.Embed(color=self.option['color']) 
            embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            embed.add_field(
                name="リアクション結果",
                value=f"{interaction.user.display_name} が '{self.option['label']}' を押しました。",
                inline=False
            )
            embed.add_field(
                name="点数",
                value=f"{self.option['score']}点",
                inline=False
            )
            embed.add_field(
                name="コメント",
                value=self.comment.value if self.comment.value else "コメントなし",
                inline=False
            )

            # スレッドにメッセージを送信
            if self.thread:
                await self.thread.send(embed=embed)
            await interaction.response.send_message("投票ありがとうございます！", ephemeral=True)
        except discord.HTTPException as e:
            logger.error(f"HTTPエラーが発生しました: {str(e)}")
            await interaction.response.send_message(f"HTTPエラーが発生しました: {str(e)}", ephemeral=True)
        except discord.Forbidden:
            logger.error("操作の権限がありません。")
            await interaction.response.send_message("この操作を行う権限がありません。", ephemeral=True)
        except discord.NotFound:
            logger.error("指定されたリソースが見つかりませんでした。")
            await interaction.response.send_message("指定されたリソースが見つかりませんでした。", ephemeral=True)
        except Exception as e:
            logger.error(f"予期しないエラーが発生しました: {str(e)}")
            await interaction.response.send_message(f"エラーが発生しました: {str(e)}", ephemeral=True)

# ボタンを作成するクラス
class ReactionButton(Button):
    def __init__(self, option, thread):
        super().__init__(label=option['label'], style=discord.ButtonStyle.primary)
        self.option = option
        self.thread = thread

    async def callback(self, interaction: discord.Interaction):
        modal = CommentModal(option=self.option, thread=self.thread)
        await interaction.response.send_modal(modal)

# Viewにボタンを追加
def create_reaction_view(target_user_id):
    thread_id = load_thread_data(target_user_id)
    thread = bot.get_channel(thread_id) if thread_id else None
    view = View(timeout=10080 * 60)  # 7日後にタイムアウト
    for option in reaction_options:
        view.add_item(ReactionButton(option=option, thread=thread))
    return view

# on_message イベントでメッセージを転記
@bot.event
async def on_message(message):
    if message.channel.id in SOURCE_CHANNEL_IDS and not message.author.bot:
        try:
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

            view = create_reaction_view(message.author.id)
            sent_message = await destination_channel.send(embed=embed, view=view)
            logger.info(f"メッセージが転記されました: {sent_message.id}")

            thread_parent_channel = bot.get_channel(THREAD_PARENT_CHANNEL_ID)
            thread = await thread_parent_channel.create_thread(
                name=f"{message.author.display_name}のリアクション投票スレッド",
                auto_archive_duration=10080  # 7日
            )
            save_thread_data(message.author.id, thread.id)
            logger.info(f"スレッドが作成されました: {thread.id} for {message.author.display_name}")
        except Exception as e:
            logger.error(f"スレッド作成またはメッセージ転記に失敗しました: {str(e)}")

# Bot再起動後にViewを再アタッチする処理
@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user}')
    create_table()
    
    destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
    async for message in destination_channel.history(limit=50):
        if message.author == bot.user and message.embeds:
            try:
                target_user_id = int(message.embeds[0].footer.text)
                view = create_reaction_view(target_user_id)
                await message.edit(view=view)
                logger.info(f"再起動後にViewを再アタッチしました: {message.id}")
            except Exception as e:
                logger.error(f"View再アタッチに失敗しました: {str(e)}")

# Botの起動
bot.run(TOKEN)
