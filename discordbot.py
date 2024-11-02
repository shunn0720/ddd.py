import discord
import os
import logging
import psycopg2
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Discord intents
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

# Environment variables
TOKEN = os.getenv('DISCORD_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# Channel settings
SOURCE_CHANNEL_IDS = [1299231408551755838, 1299231612944257036]
DESTINATION_CHANNEL_ID = 1299231533437292596
THREAD_PARENT_CHANNEL_ID = 1299231693336743996

# Reaction options with colors and scores
reaction_options = [
    {"label": "入ってほしい！", "style": discord.ButtonStyle.success, "score": 2, "custom_id": "type1"},
    {"label": "良い人！", "style": discord.ButtonStyle.success, "score": 1, "custom_id": "type2"},
    {"label": "微妙", "style": discord.ButtonStyle.danger, "score": -1, "custom_id": "type3"},
    {"label": "入ってほしくない", "style": discord.ButtonStyle.danger, "score": -2, "custom_id": "type4"}
]

# Tracks threads per user
user_threads = {}

# Bot setup
bot = commands.Bot(command_prefix='!', intents=intents)

def get_db_connection():
    """Connect to the database with retries."""
    try:
        connection = psycopg2.connect(DATABASE_URL, sslmode='require')
        logger.info("Database connected successfully")
        return connection
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return None

def save_user_thread(user_id, thread_id):
    """Save or update a user's thread ID in the database."""
    connection = get_db_connection()
    if connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO user_threads (user_id, thread_id)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET thread_id = EXCLUDED.thread_id;
                """, (user_id, thread_id))
                connection.commit()
            logger.info(f"Saved thread ID {thread_id} for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to save thread ID to database: {e}")
        finally:
            connection.close()

def fetch_user_thread(user_id):
    """Fetch a user's thread ID from the database."""
    connection = get_db_connection()
    if connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT thread_id FROM user_threads WHERE user_id = %s", (user_id,))
                result = cursor.fetchone()
            return result[0] if result else None
        except Exception as e:
            logger.error(f"Failed to fetch thread ID: {e}")
        finally:
            connection.close()
    return None

# Modal for comments
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
        embed = discord.Embed(color=option['style'].value)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="リアクション結果", value=f"{interaction.user.display_name} が '{option['label']}' を押しました。", inline=False)
        embed.add_field(name="点数", value=f"{option['score']}点", inline=False)
        embed.add_field(name="コメント", value=self.comment.value if self.comment.value else "コメントなし", inline=False)

        # Delete previous votes from this user
        async for msg in self.thread.history(limit=100):
            if msg.author == bot.user and msg.embeds and msg.embeds[0].author.name == interaction.user.display_name:
                await msg.delete()

        await self.thread.send(embed=embed)
        await interaction.response.send_message("投票を完了しました！", ephemeral=True)

# Buttons with reaction functionality
class ReactionButton(Button):
    def __init__(self, label, style, score, reaction_type, thread):
        super().__init__(label=label, style=style)
        self.score = score
        self.reaction_type = reaction_type
        self.thread = thread

    async def callback(self, interaction: discord.Interaction):
        modal = CommentModal(self.reaction_type, self.thread)
        await interaction.response.send_modal(modal)

def create_reaction_view(thread):
    view = View(timeout=None)
    for i, option in enumerate(reaction_options):
        view.add_item(ReactionButton(label=option["label"], style=option["style"], score=option["score"], reaction_type=i, thread=thread))
    return view

# Message forwarding and thread creation
@bot.event
async def on_message(message):
    if message.channel.id in SOURCE_CHANNEL_IDS and not message.author.bot:
        destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
        if destination_channel is None:
            logger.error("Destination channel not found.")
            return

        # Forward the message and create an embed
        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.add_field(
            name="🌱つぼみ審査投票フォーム",
            value="必ずこのサーバーでお話した上で投票をお願いします。\n複数回投票した場合は最新のものを反映します。\nこの方の入場について、NG等意見のある方はお問い合わせください。",
            inline=False
        )

        sent_message = await destination_channel.send(embed=embed, view=create_reaction_view(message.author))
        thread_parent_channel = bot.get_channel(THREAD_PARENT_CHANNEL_ID)
        if thread_parent_channel is None:
            logger.error("Thread parent channel not found.")
            return

        # Create a thread and save its ID
        try:
            thread = await thread_parent_channel.create_thread(name=f"{message.author.display_name}の投票スレッド")
            save_user_thread(message.author.id, thread.id)
            user_threads[message.author.id] = thread
            logger.info(f"Message forwarded and thread created for {message.author.display_name}")
        except Exception as e:
            logger.error(f"Failed to create thread: {e}")

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user}")

bot.run(TOKEN)
