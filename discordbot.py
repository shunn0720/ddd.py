import discord
import os
import logging
import psycopg2
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput

# ログの設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

# Herokuの環境変数からトークンとデータベースURLを取得
TOKEN = os.getenv('DISCORD_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# チャンネルIDを設定
SOURCE_CHANNEL_IDS = [1299231408551755838, 1299231612944257036]
DESTINATION_CHANNEL_ID = 1299231533437292596
THREAD_PARENT_CHANNEL_ID = 1299231693336743996

# データベース接続とテーブル作成
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def create_table():
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS thread_data (
                    target_user_id BIGINT PRIMARY KEY,
                    thread_id BIGINT NOT NULL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vote_data (
                    voter_id BIGINT,
                    target_user_id BIGINT,
                    message_id BIGINT,
                    PRIMARY KEY (voter_id, target_user_id)
                )
            """)
        conn.commit()

# スレッドデータの保存と読み込み
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

def load_thread_data(target_user_id):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT thread_id FROM thread_data WHERE target_user_id = %s", (target_user_id,))
            result = cursor.fetchone()
            return result[0] if result else None

# 投票データの保存と削除
def save_vote_data(voter_id, target_user_id, message_id):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO vote_data (voter_id, target_user_id, message_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (voter_id, target_user_id) DO UPDATE
                SET message_id = EXCLUDED.message_id
            """, (voter_id, target_user_id, message_id))
        conn.commit()

def load_vote_data(voter_id, target_user_id):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT message_id FROM vote_data WHERE voter_id = %s AND target_user_id = %s", (voter_id, target_user_id))
            result = cursor.fetchone()
            return result[0] if result else None

def delete_vote_data(voter_id, target_user_id):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM vote_data WHERE voter_id = %s AND target_user_id = %s", (voter_id, target_user_id))
        conn.commit()

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
    def __init__(self, option, target_user, thread):
        super().__init__(title="投票画面")
        self.option = option
        self.target_user = target_user
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
            # 既存の投票がある場合は削除
            existing_message_id = load_vote_data(interaction.user.id, self.target_user.id)
            if existing_message_id:
                existing_message = await self.thread.fetch_message(existing_message_id)
                await existing_message.delete()
            
            # 新しい投票結果をEmbedとして作成
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

            # スレッドにメッセージを送信してメッセージIDを保存
            sent_message = await self.thread.send(embed=embed)
            save_vote_data(interaction.user.id, self.target_user.id, sent_message.id)
            await interaction.response.send_message("投票ありがとう！", ephemeral=True)

        except Exception as e:
            logger.error(f"エラーが発生しました: {e}")
            await interaction.response.send_message("エラーが発生しました。再度お試しください。", ephemeral=True)

# ボタンをクリックしたときの処理
class ReactionButton(Button):
    def __init__(self, option, target_user):
        super().__init__(label=option["label"], style=discord.ButtonStyle.primary)
        self.option = option
        self.target_user = target_user

    async def callback(self, interaction: discord.Interaction):
        thread_id = load_thread_data(self.target_user.id)
        if not thread_id:
            await interaction.response.send_message("スレッドが見つかりませんでした。", ephemeral=True)
            return

        thread = bot.get_channel(thread_id)
        if not thread:
            await interaction.response.send_message("スレッドにアクセスできません。", ephemeral=True)
            return

        modal = CommentModal(self.option, self.target_user, thread)
        await interaction.response.send_modal(modal)

# Viewにボタンを追加
def create_reaction_view(target_user):
    view = View(timeout=None)
    for option in reaction_options:
        view.add_item(ReactionButton(option=option, target_user=target_user))
    return view

# on_message イベントでメッセージを転記
@bot.event
async def on_message(message):
    if message.channel.id in SOURCE_CHANNEL_IDS and not message.author.bot:
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)

        # メッセージの送信者のEmbedを作成して転記
        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.add_field(
            name="🌱つぼみ審査投票フォーム",
            value=(
                "必ずこのサーバーでお話した上で投票をお願いします。\n"
                "複数回投票した場合は、最新のものを反映します。\n"
                "この方の入場について、NG等意見のある方はお問い合わせください。"
            ),
            inline=False
        )

        view = create_reaction_view(message.author)
        sent_message = await destination_channel.send(embed=embed, view=view)
        logger.info(f"メッセージが転記されました: {sent_message.id}")

        # スレッド作成
        thread_parent_channel = bot.get_channel(THREAD_PARENT_CHANNEL_ID)
        try:
            thread = await thread_parent_channel.create_thread(
                name=f"{message.author.display_name}のリアクション投票スレッド",
                auto_archive_duration=10080  # 7日
            )
            save_thread_data(message.author.id, thread.id)  # スレッドデータをデータベースに保存
            logger.info(f"スレッドが作成されました: {thread.id} for {message.author.display_name}")
        except Exception as e:
            logger.error(f"スレッド作成に失敗しました: {e}")

# Bot再起動後にViewを再アタッチする処理
@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user}')
    create_table()  # テーブル作成
    destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
    async for message in destination_channel.history(limit=20):  
        if message.author == bot.user and message.embeds:
            try:
                user_id = int(message.embeds[0].thumbnail.url.split("/")[4])
                author = await bot.fetch_user(user_id)
                if author:
                    view = create_reaction_view(author)
                    await message.edit(view=view)
                    logger.info(f"再起動後にViewを再アタッチしました: {message.id}")
            except Exception as e:
                logger.error(f"再アタッチに失敗しました: {e}")

# Botの起動
bot.run(TOKEN)
