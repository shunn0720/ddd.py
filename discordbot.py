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
DATABASE_URL = os.getenv("DATABASE_URL")
TOKEN = os.getenv("DISCORD_TOKEN")  # Herokuの環境変数名に合わせて修正

# TOKEN の存在確認を追加
if TOKEN is None:
    logger.error("環境変数 DISCORD_TOKEN が設定されていません。Herokuのダッシュボードで設定してください。")
    exit(1)  # プログラムを終了

# チャンネルIDを設定
SOURCE_CHANNEL_IDS = [1289481073259970592]
DESTINATION_CHANNEL_ID = 1290017703456804958
THREAD_PARENT_CHANNEL_ID = 1289867786180624496 

# ボタンの選択肢とスコア
reaction_options = [
    {"label": "入ってほしい！", "color": discord.Color.green(), "score": 2, "custom_id": "type1"},
    {"label": "良い人！", "color": discord.Color.green(), "score": 1, "custom_id": "type2"},
    {"label": "微妙", "color": discord.Color.red(), "score": -1, "custom_id": "type3"},
    {"label": "入ってほしくない", "color": discord.Color.red(), "score": -2, "custom_id": "type4"}
]

# データベースの初期化
def init_db():
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='disable')
        with conn.cursor() as cur:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS user_threads (
                    user_id BIGINT PRIMARY KEY,
                    thread_id BIGINT NOT NULL
                )
            ''')
        conn.commit()
        logger.info("データベースが初期化されました")
    except Exception as e:
        logger.error(f"データベースの初期化に失敗しました: {e}")
    finally:
        if conn:
            conn.close()

# スレッドデータをデータベースに保存
def save_thread_data(user_id, thread_id):
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='disable')
        with conn.cursor() as cur:
            cur.execute('''
                INSERT INTO user_threads (user_id, thread_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE
                SET thread_id = EXCLUDED.thread_id
            ''', (user_id, thread_id))
        conn.commit()
        logger.info(f"スレッドデータが保存されました: user_id={user_id}, thread_id={thread_id}")
    except Exception as e:
        logger.error(f"スレッドデータの保存に失敗しました: {e}")
    finally:
        if conn:
            conn.close()

# スレッドデータをデータベースから取得
def get_thread_data(user_id):
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='disable')
        with conn.cursor() as cur:
            cur.execute('SELECT thread_id FROM user_threads WHERE user_id = %s', (user_id,))
            result = cur.fetchone()
            return result[0] if result else None
    except Exception as e:
        logger.error(f"スレッドデータの取得に失敗しました: {e}")
        return None
    finally:
        if conn:
            conn.close()

# Bot設定
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user}')
    init_db()

# ボタン押下時の処理
async def on_button_click(interaction: discord.Interaction):
    custom_id = interaction.data["custom_id"]
    modal = CommentModal(type=int(custom_id[-1]))  # カスタムIDの最後の数字を使用
    await interaction.response.send_modal(modal)

class CommentModal(Modal):
    def __init__(self, type):
        super().__init__(title="投票画面", custom_id=str(type))

        self.comment = TextInput(
            label="コメント",
            style=discord.TextStyle.paragraph,
            placeholder="理由がある場合はこちらに入力してください（そのまま送信も可）",
            required=False
        )
        self.add_item(self.comment)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            custom_id = interaction.data["custom_id"]
            option_index = int(custom_id[-1])
        
            if option_index < 0 or option_index >= len(reaction_options):
                await interaction.response.send_message("無効なオプションが選択されました。", ephemeral=True)
                return

            option = reaction_options[option_index]
            thread_id = get_thread_data(interaction.user.id)

            if thread_id is None:
                await interaction.response.send_message("スレッドが見つかりませんでした。", ephemeral=True)
                return

            thread = bot.get_channel(thread_id)
            if thread is None:
                await interaction.response.send_message("スレッドが見つかりませんでした。", ephemeral=True)
                return

            embed = discord.Embed(color=option['color'])
            embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            embed.add_field(name="リアクション結果", value=f"{interaction.user.display_name} が '{option['label']}' を押しました。", inline=False)
            embed.add_field(name="点数", value=f"{option['score']}点", inline=False)
            embed.add_field(name="コメント", value=self.comment.value if self.comment.value else "コメントなし", inline=False)

            await thread.send(embed=embed)
            await interaction.response.send_message("投票ありがとう！", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"エラーが発生しました: {str(e)}", ephemeral=True)
            logger.error(f"投票時のエラー: {e}")

# ボタンのクラス
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
async def on_interaction(interaction:discord.Interaction):
    try:
        if interaction.data['component_type'] == 2:
            await on_button_click(interaction)
    except KeyError:
        pass

@bot.event
async def on_message(message):
    if message.channel.id in SOURCE_CHANNEL_IDS and not message.author.bot:
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
        if destination_channel is None:
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
            sent_message = await destination_channel.send(embed=embed, view=view)
        except discord.HTTPException as e:
            logger.error(f"メッセージの送信に失敗しました: {e}")
            return

        thread_parent_channel = bot.get_channel(THREAD_PARENT_CHANNEL_ID)
        if thread_parent_channel is None:
            logger.error("スレッド親チャンネルが見つかりませんでした。")
            return

        try:
            thread = await thread_parent_channel.create_thread(name=f"{message.author.display_name}のリアクション投票スレッド", auto_archive_duration=10080)
            save_thread_data(message.author.id, thread.id)
        except discord.HTTPException as e:
            logger.error(f"スレッド作成に失敗しました: {e}")

# Botの起動
bot.run(TOKEN)
