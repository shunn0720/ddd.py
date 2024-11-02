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

# 環境変数からトークンとデータベースURLを取得
TOKEN = os.getenv('DISCORD_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# チャンネルIDを設定
SOURCE_CHANNEL_IDS = [1299231408551755838, 1299231612944257036]
DESTINATION_CHANNEL_ID = 1299231533437292596
THREAD_PARENT_CHANNEL_ID = 1299231693336743996

# ボタンの選択肢とスコア
reaction_options = [
    {"label": "入ってほしい！", "color": discord.ButtonStyle.green, "score": 2, "custom_id": "type1"},
    {"label": "良い人！", "color": discord.ButtonStyle.green, "score": 1, "custom_id": "type2"},
    {"label": "微妙", "color": discord.ButtonStyle.red, "score": -1, "custom_id": "type3"},
    {"label": "入ってほしくない", "color": discord.ButtonStyle.red, "score": -2, "custom_id": "type4"}
]

# Bot設定
bot = commands.Bot(command_prefix='!', intents=intents)

# データベース接続
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

# スレッドIDを保存する関数
def save_thread_to_db(user_id, thread_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO user_threads (user_id, thread_id) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET thread_id = EXCLUDED.thread_id",
                    (user_id, thread_id)
                )
                conn.commit()
                logger.info(f"スレッドID {thread_id} をユーザー {user_id} に対して保存しました。")
    except Exception as e:
        logger.error(f"スレッドIDの保存に失敗しました: {e}")

# スレッドIDをデータベースから取得する関数
def get_thread_from_db(user_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT thread_id FROM user_threads WHERE user_id = %s", (user_id,))
                result = cur.fetchone()
                return result[0] if result else None
    except Exception as e:
        logger.error(f"スレッドIDの取得に失敗しました: {e}")
        return None

# コメントを入力するためのモーダル
class CommentModal(Modal):
    def __init__(self, reaction_type, thread):
        super().__init__(title="投票画面", timeout=None)

        self.comment = TextInput(
            label="コメント",
            style=discord.TextStyle.paragraph,
            placeholder="理由がある場合はこちらに入力してください（そのまま送信も可）",
            required=False
        )
        self.add_item(self.comment)
        self.reaction_type = reaction_type
        self.thread = thread

    async def on_submit(self, interaction: discord.Interaction):
        option = reaction_options[self.reaction_type]
        embed = discord.Embed(color=option['color'].value)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="リアクション結果", value=f"{interaction.user.display_name} が '{option['label']}' を押しました。", inline=False)
        embed.add_field(name="点数", value=f"{option['score']}点", inline=False)
        embed.add_field(name="コメント", value=self.comment.value if self.comment.value else "コメントなし", inline=False)

        await self.thread.send(embed=embed)
        await interaction.response.send_message("投票を完了しました！", ephemeral=True)

# ボタンを作成するクラス
class ReactionButton(Button):
    def __init__(self, label, color, score, custom_id, reaction_type, thread):
        super().__init__(label=label, style=color, custom_id=custom_id)
        self.reaction_type = reaction_type
        self.thread = thread

    async def callback(self, interaction: discord.Interaction):
        modal = CommentModal(self.reaction_type, self.thread)
        await interaction.response.send_modal(modal)

# Viewにボタンを追加
def create_reaction_view(thread):
    view = View(timeout=None)
    for i, option in enumerate(reaction_options):
        view.add_item(ReactionButton(label=option["label"], color=option["color"], score=option["score"], custom_id=option["custom_id"], reaction_type=i, thread=thread))
    return view

# on_message イベントでメッセージを転記してスレッドを作成
@bot.event
async def on_message(message):
    if message.channel.id in SOURCE_CHANNEL_IDS and not message.author.bot:
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)

        # メッセージの送信者のEmbedを作成して転記
        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(name=message.author.display_name)
        
        # 右に大きくアイコンを表示
        embed.set_image(url=message.author.display_avatar.url)
        
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
        logger.info(f"メッセージが転記されました: {sent_message.id}")  # ログ出力

        # スレッド作成
        thread_parent_channel = bot.get_channel(THREAD_PARENT_CHANNEL_ID)
        if thread_parent_channel:
            try:
                thread = await thread_parent_channel.create_thread(
                    name=f"{message.author.display_name}のリアクション投票スレッド",
                    auto_archive_duration=10080
                )
                save_thread_to_db(message.author.id, thread.id)
                view = create_reaction_view(thread)
                await sent_message.edit(view=view)
                logger.info(f"スレッドが作成されました: {thread.id} for {message.author.display_name}")
            except Exception as e:
                logger.error(f"スレッド作成に失敗しました: {e}")

# Bot再起動後にViewを再アタッチする処理
@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user.name}#{bot.user.discriminator}")
    
    destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
    if destination_channel:
        async for message in destination_channel.history(limit=50):
            if message.author == bot.user and message.embeds:
                user_id = int(message.embeds[0].author.name.split("#")[0])
                thread_id = get_thread_from_db(user_id)
                if thread_id:
                    thread = await bot.fetch_channel(thread_id)
                    if thread:
                        view = create_reaction_view(thread)
                        await message.edit(view=view)
                        logger.info(f"再起動後にViewを再アタッチしました: {message.id}")

# Botの起動
bot.run(TOKEN)
