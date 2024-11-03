import discord
from discord.ext import commands
import logging
import os
import asyncpg

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# データベース接続の初期化
async def init_db():
    bot.db = await asyncpg.create_pool(DATABASE_URL)
    logging.info("Database connected successfully")

@bot.event
async def on_ready():
    logging.info(f"Logged in as {bot.user}")

# ボタンがクリックされたときの処理
class CustomView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Open Modal", style=discord.ButtonStyle.primary)
    async def open_modal(self, button: discord.ui.Button, interaction: discord.Interaction):
        logging.info(f"Button clicked by {interaction.user.id}, attempting to open modal for user {self.user_id}")
        try:
            await interaction.response.send_modal(CommentModal(self.user_id))
        except discord.errors.InteractionResponded:
            logging.error("Failed to send modal: This interaction has already been responded to before")

# モーダルの設定
class CommentModal(discord.ui.Modal):
    def __init__(self, user_id):
        super().__init__(title="Submit a Comment")
        self.user_id = user_id
        self.add_item(discord.ui.InputText(label="Comment"))

    async def fetch_thread(self):
        async with bot.db.acquire() as connection:
            result = await connection.fetchrow("SELECT thread_id FROM user_threads WHERE user_id = $1", self.user_id)
            if result:
                thread_id = result["thread_id"]
                try:
                    thread = await bot.fetch_channel(thread_id)
                    return thread
                except discord.NotFound:
                    logging.error(f"Thread {thread_id} not found.")
                    return None
            return None

    async def on_submit(self, interaction: discord.Interaction):
        thread = await self.fetch_thread()
        if thread is None:
            await interaction.response.send_message("The thread could not be found or may have been deleted.", ephemeral=True)
            return

        if thread.archived:
            await thread.unarchive()

        comment = self.children[0].value
        await thread.send(content=comment)
        await interaction.response.send_message("Your comment has been posted!", ephemeral=True)

# コマンドでスレッドを作成してメッセージを転送
@bot.command()
async def forward(ctx, user_id: int, *, content: str):
    try:
        async with bot.db.acquire() as connection:
            thread = await ctx.channel.create_thread(name=f"{ctx.author.name}'s thread", type=discord.ChannelType.public_thread)
            await thread.send(content=content)
            await connection.execute("INSERT INTO user_threads (user_id, thread_id) VALUES ($1, $2)", user_id, thread.id)
            logging.info(f"Saved thread ID {thread.id} for user {user_id}")
            await ctx.send(f"Message forwarded and thread created for {ctx.author.name} with thread ID {thread.id}")
    except discord.errors.HTTPException as e:
        logging.error(f"Failed to create thread: {e}")

# データベース初期化
@bot.event
async def on_connect():
    await init_db()

bot.run(TOKEN)
