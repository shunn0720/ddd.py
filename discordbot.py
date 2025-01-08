import discord
from discord.ext import commands, tasks
import random
import asyncio
import logging
import psycopg2
from psycopg2 import pool, Error
from psycopg2.extras import DictCursor
from dotenv import load_dotenv
import os
import json

########################
# .env 環境変数読み込み
########################
load_dotenv()

########################
# ログレベルの設定
########################
DEBUG_MODE = os.getenv("DEBUG_MODE", "False").lower() in ("true", "1", "t")
log_level = logging.DEBUG if DEBUG_MODE else logging.INFO

logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

########################
# 環境変数・定数
########################
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

THREAD_ID = os.getenv("THREAD_ID")
if THREAD_ID is None:
    logging.error("THREAD_IDが設定されていません。環境変数を確認してください。")
    exit(1)

try:
    THREAD_ID = int(THREAD_ID)
except ValueError:
    logging.error("THREAD_IDが無効な値です。正しいチャンネルID(数値)を設定してください。")
    exit(1)

########################
# リアクションIDの定義
########################
REACTIONS = {
    "b431": 1289782471197458495,  # <:b431:1289782471197458495> (ランダム除外)
    "b434": 1304690617405669376,  # <:b434:1304690617405669376> (あとで読む)
    "b435": 1304690627723657267,  # <:b435:1304690627723657267> (お気に入り)
}

READ_LATER_REACTION_ID = REACTIONS["b434"]  # あとで読む
FAVORITE_REACTION_ID   = REACTIONS["b435"]  # お気に入り
RANDOM_EXCLUDE_ID      = REACTIONS["b431"]  # ランダム除外
SPECIFIC_EXCLUDE_USER  = 695096014482440244     # 特定投稿者 (例)

########################
# DB接続プール
########################
try:
    db_pool = pool.SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        dsn=DATABASE_URL,
        sslmode='require'
    )
    logging.info("Database connection pool initialized.")
except Error as e:
    logging.error(f"Database connection pool initialization error: {e}")
    db_pool = None

def get_db_connection():
    try:
        if db_pool:
            return db_pool.getconn()
        else:
            raise Error("Database connection pool is not initialized.")
    except Error as e:
        logging.error(f"Error getting database connection: {e}")
        return None

def release_db_connection(conn):
    try:
        if db_pool and conn:
            db_pool.putconn(conn)
    except Error as e:
        logging.error(f"Error releasing database connection: {e}")

def initialize_db():
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                message_id BIGINT NOT NULL UNIQUE,
                thread_id BIGINT NOT NULL,
                author_id BIGINT NOT NULL,
                reactions JSONB DEFAULT '{}',
                content TEXT
            )
            """)
            conn.commit()
        logging.info("Database initialized successfully.")
    except Error as e:
        logging.error(f"Error initializing tables: {e}")
    finally:
        release_db_connection(conn)

initialize_db()

########################
# Botインテンツの設定
########################
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.reactions = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

########################
# ヘルパー変数・関数
########################
last_chosen_authors = {}

async def safe_fetch_message(channel, message_id):
    try:
        return await channel.fetch_message(message_id)
    except (discord.NotFound, discord.HTTPException):
        return None

async def ensure_message_in_db(message):
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT id FROM messages WHERE message_id = %s", (message.id,))
            row = cur.fetchone()
            if row:
                return

            # リアクション収集は同期タスクに任せるため削除
            cur.execute("""
                INSERT INTO messages (message_id, thread_id, author_id, content)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (message.id, message.channel.id, message.author.id, message.content))
            conn.commit()
            logging.info(f"Inserted new message into DB (message_id={message.id}).")
    except Error as e:
        logging.error(f"Error ensuring message in DB: {e}")
    finally:
        release_db_connection(conn)

async def update_reactions_in_db(message_id, emoji_id, user_id, add=True):
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT reactions FROM messages WHERE message_id = %s", (message_id,))
            row = cur.fetchone()
            if not row:
                logging.info(f"No row found for message_id={message_id}, skip reaction update.")
                return
            reactions = row['reactions'] or {}
            if isinstance(reactions, str):
                try:
                    reactions = json.loads(reactions)
                except json.JSONDecodeError:
                    reactions = {}

            str_emoji_id = str(emoji_id)
            user_list = reactions.get(str_emoji_id, [])

            if add:
                if user_id not in user_list:
                    user_list.append(user_id)
                    logging.debug(f"Added user_id={user_id} to reaction_id={emoji_id} for message_id={message_id}.")
            else:
                if user_id in user_list:
                    user_list.remove(user_id)
                    logging.debug(f"Removed user_id={user_id} from reaction_id={emoji_id} for message_id={message_id}.")

            reactions[str_emoji_id] = user_list
            new_json = json.dumps(reactions)
            logging.debug(f"Updated reactions for message_id={message_id}: {new_json}")

            cur.execute("""
                UPDATE messages
                SET reactions = %s
                WHERE message_id = %s
            """, (new_json, message_id))
            conn.commit()
            logging.info(f"Reactions updated for message_id={message_id}. Current reactions: {new_json}")
    except Error as e:
        logging.error(f"Error updating reactions in DB: {e}")
    finally:
        release_db_connection(conn)

def user_reacted(msg, reaction_id, user_id):
    reaction_data = msg.get('reactions', {})
    if isinstance(reaction_data, str):
        try:
            reaction_data = json.loads(reaction_data)
        except json.JSONDecodeError:
            reaction_data = {}
    users = reaction_data.get(str(reaction_id), [])
    logging.debug(f"user_reacted: reaction_id={reaction_id}, user_id={user_id}, users={users}")
    return (user_id in users)

async def get_random_message(thread_id, filter_func=None, button_name="N/A"):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
            rows = cur.fetchall()

            for m in rows:
                if m['reactions'] is None:
                    m['reactions'] = {}
                elif isinstance(m['reactions'], str):
                    try:
                        m['reactions'] = json.loads(m['reactions']) or {}
                    except json.JSONDecodeError:
                        m['reactions'] = {}

            logging.info(f"[DEBUG] [{button_name}] get_random_message: total {len(rows)} messages before filter.")

            if filter_func:
                filtered = []
                for row in rows:
                    if filter_func(row):
                        filtered.append(row)
                logging.info(f"[DEBUG] [{button_name}] get_random_message: after filter -> {len(filtered)} messages remain.")
                rows = filtered

            if not rows:
                return None
            return random.choice(rows)
    except Error as e:
        logging.error(f"Error fetching random message: {e}")
        return None
    finally:
        release_db_connection(conn)

########################
# Viewクラス
########################
class CombinedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def get_author_name(self, author_id):
        user = bot.get_user(author_id)
        if user is None:
            try:
                user = await bot.fetch_user(author_id)
            except discord.NotFound:
                user = None
        if user:
            return user.display_name or user.name
        else:
            return f"UnknownUser({author_id})"

    async def handle_selection(self, interaction, random_message, user_id):
        if random_message:
            last_chosen_authors[user_id] = random_message['author_id']
            author_name = await self.get_author_name(random_message['author_id'])
            # 単純メッセージ送信
            await interaction.channel.send(
                f"{interaction.user.mention} さんには、{author_name} さんの投稿がおすすめです！\n"
                f"https://discord.com/channels/{interaction.guild_id}/{THREAD_ID}/{random_message['message_id']}"
            )
        else:
            await interaction.channel.send(
                f"{interaction.user.mention} さん、該当する投稿が見つかりませんでした。"
            )

        # パネルを再送信
        await send_panel(interaction.channel)

    async def get_and_handle_random_message(self, interaction, filter_func, button_name="N/A"):
        random_msg = await get_random_message(THREAD_ID, filter_func=filter_func, button_name=button_name)
        await self.handle_selection(interaction, random_msg, interaction.user.id)

    # --- 青ボタン：ランダム ---
    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary, row=0, custom_id="blue_random")
    async def blue_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "blue_random"
        def filter_func(msg):
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same author as last selection.")
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

    # --- 青ボタン：あとで読む (b434) ---
    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary, row=0, custom_id="read_later")
    async def read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "blue_read_later"
        def filter_func(msg):
            if not user_reacted(msg, READ_LATER_REACTION_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: no b434 from user.")
                return False
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same author as last selection.")
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

    # --- 青ボタン：お気に入り (b435) ---
    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary, row=0, custom_id="favorite")
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "blue_favorite"
        def filter_func(msg):
            logging.debug(f"DB reactions for msg_id={msg['message_id']}: {msg['reactions']}")
            if not user_reacted(msg, FAVORITE_REACTION_ID, interaction.user.id):
                logging.debug(
                    f"Excluding msg_id={msg['message_id']}: reaction check failed, "
                    f"FAVORITE_REACTION_ID={FAVORITE_REACTION_ID}, user_id={interaction.user.id}, reactions={msg['reactions']}"
                )
                return False
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same author as last selection.")
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

    # --- 赤ボタン：ランダム ---
    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.danger, row=1, custom_id="red_random")
    async def red_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "red_random"
        def filter_func(msg):
            if user_reacted(msg, RANDOM_EXCLUDE_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: user has b431.")
                return False
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            if msg['author_id'] == SPECIFIC_EXCLUDE_USER:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: specific exclude author.")
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same author as last selection.")
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

    # --- 赤ボタン：あとで読む (b434) + b431除外 ---
    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.danger, row=1, custom_id="conditional_read_later")
    async def conditional_read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "red_read_later"
        def filter_func(msg):
            if not user_reacted(msg, READ_LATER_REACTION_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: no b434 from user.")
                return False
            if user_reacted(msg, RANDOM_EXCLUDE_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: user has b431.")
                return False
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            if last_chosen_authors.get(interaction.user.id) == msg['author_id']:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same author as last selection.")
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

current_panel_message_id = None

async def send_panel(channel):
    global current_panel_message_id
    if current_panel_message_id:
        try:
            old_msg = await channel.fetch_message(current_panel_message_id)
            await old_msg.delete()
            logging.info(f"Deleted previous panel message with ID {current_panel_message_id}.")
        except discord.NotFound:
            logging.warning(f"Previous panel message with ID {current_panel_message_id} not found.")
        except discord.HTTPException as e:
            logging.error(f"Error deleting panel message: {e}")

    embed = create_panel_embed()
    view = CombinedView()
    try:
        sent_msg = await channel.send(embed=embed, view=view)
        current_panel_message_id = sent_msg.id
        logging.info(f"Sent new panel message with ID {current_panel_message_id}.")
    except discord.HTTPException as e:
        logging.error(f"Error sending panel message: {e}")

def create_panel_embed():
    embed = discord.Embed(
        title="🎯 エロ漫画ルーレット",
        description=(
            "botがエロ漫画を選んでくれるよ！\n\n"
            "🔵：自分の <:b431:1289782471197458495> を除外しない\n"
            "🔴：自分の <:b431:1289782471197458495> を除外する\n\n"
            "**ランダム**：全体から選ぶ\n"
            "**あとで読む**：<:b434:1304690617405669376> を付けた投稿\n"
            "**お気に入り**：<:b435:1304690627723657267> を付けた投稿"
        ),
        color=0xFF69B4
    )
    return embed

########################
# スラッシュコマンド
########################
@bot.tree.command(name="panel", description="ルーレット用パネルを表示します。")
async def panel(interaction: discord.Interaction):
    channel = interaction.channel
    if channel:
        await interaction.response.send_message("パネルを表示します！", ephemeral=True)
        await send_panel(channel)
    else:
        await interaction.response.send_message("エラー: チャンネルが取得できませんでした。", ephemeral=True)

@bot.tree.command(name="check_reactions", description="特定のメッセージのリアクションを表示します。")
async def check_reactions(interaction: discord.Interaction, message_id: str):
    try:
        msg_id = int(message_id)
    except ValueError:
        await interaction.response.send_message("無効なメッセージIDです。", ephemeral=True)
        return

    conn = get_db_connection()
    if not conn:
        await interaction.response.send_message("DB接続に失敗しました。", ephemeral=True)
        return

    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT reactions FROM messages WHERE message_id = %s", (msg_id,))
            row = cur.fetchone()
            if not row:
                await interaction.response.send_message("DBにそのメッセージが存在しません。", ephemeral=True)
                return

            r = row['reactions'] or {}
            if isinstance(r, str):
                try:
                    r = json.loads(r)
                except json.JSONDecodeError:
                    r = {}

            if not r:
                await interaction.response.send_message("リアクションはありません。", ephemeral=True)
            else:
                embed = discord.Embed(
                    title=f"Message ID: {msg_id} のリアクション情報",
                    color=0x00FF00
                )
                for emoji_id, user_ids in r.items():
                    try:
                        emoji_obj = bot.get_emoji(int(emoji_id))
                        if emoji_obj:
                            emoji_str = str(emoji_obj)
                        else:
                            emoji_str = f"UnknownEmoji({emoji_id})"
                    except ValueError:
                        emoji_str = f"InvalidEmojiID({emoji_id})"

                    embed.add_field(
                        name=emoji_str,
                        value=f"{len(user_ids)} 人: {user_ids}",
                        inline=False
                    )
                await interaction.response.send_message(embed=embed, ephemeral=True)
    except Error as e:
        logging.error(f"Error fetching reactions for message_id={msg_id}: {e}")
        await interaction.response.send_message("リアクション取得中にエラーが発生しました。", ephemeral=True)
    finally:
        release_db_connection(conn)

@bot.tree.command(name="migrate_reactions", description="既存のメッセージのリアクションをデータベースに保存します。")
@discord.app_commands.checks.has_permissions(administrator=True)
async def migrate_reactions(interaction: discord.Interaction):
    await interaction.response.send_message("リアクションの移行を開始します。しばらくお待ちください...", ephemeral=True)
    channel = bot.get_channel(THREAD_ID)
    if channel is None:
        await interaction.followup.send("指定したTHREAD_IDのチャンネルが見つかりませんでした。", ephemeral=True)
        return

    all_messages = []
    try:
        async for message in channel.history(limit=None):
            all_messages.append(message)
    except discord.HTTPException as e:
        logging.error(f"Error fetching message history for migration: {e}")
        await interaction.followup.send("メッセージ履歴の取得中にエラーが発生しました。", ephemeral=True)
        return

    success_count = 0
    for message in all_messages:
        await ensure_message_in_db(message)
        # Fetch reactions
        try:
            message = await channel.fetch_message(message.id)
            reactions = message.reactions
            for reaction in reactions:
                if reaction.emoji.id not in REACTIONS.values():
                    continue
                async for user in reaction.users():
                    if user.id == bot.user.id:
                        continue
                    await update_reactions_in_db(message.id, reaction.emoji.id, user.id, add=True)
            success_count += 1
            # Optional: Add a short delay to prevent rate limiting
            await asyncio.sleep(0.1)
        except discord.HTTPException as e:
            logging.error(f"Error fetching reactions for message_id={message.id}: {e}")

    await interaction.followup.send(f"リアクションの移行が完了しました。{success_count} 件のメッセージを処理しました。", ephemeral=True)

########################
# リアクションイベント
########################
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    logging.info(f"on_raw_reaction_add fired: emoji={payload.emoji}, user_id={payload.user_id}, message_id={payload.message_id}")

    if payload.user_id == bot.user.id:
        logging.debug("Reaction added by the bot itself; ignoring.")
        return

    if payload.emoji.id not in REACTIONS.values():
        logging.debug(f"Ignoring reaction with emoji_id={payload.emoji.id} (not in target reactions).")
        return

    channel = bot.get_channel(payload.channel_id)
    if not channel:
        logging.info("channel is None, cannot process reaction.")
        return

    message = await safe_fetch_message(channel, payload.message_id)
    if not message:
        logging.info(f"message_id={payload.message_id} not found in channel.")
        return

    await ensure_message_in_db(message)
    await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=True)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    logging.info(f"on_raw_reaction_remove fired: emoji={payload.emoji}, user_id={payload.user_id}, message_id={payload.message_id}")

    if payload.user_id == bot.user.id:
        logging.debug("Reaction removed by the bot itself; ignoring.")
        return

    if payload.emoji.id not in REACTIONS.values():
        logging.debug(f"Ignoring reaction removal with emoji_id={payload.emoji.id} (not in target reactions).")
        return

    channel = bot.get_channel(payload.channel_id)
    if not channel:
        logging.info("channel is None, cannot process reaction removal.")
        return

    message = await safe_fetch_message(channel, payload.message_id)
    if not message:
        logging.info(f"message_id={payload.message_id} not found in channel.")
        return

    await ensure_message_in_db(message)
    await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=False)

########################
# 起動時の処理
########################
@bot.event
async def on_ready():
    logging.info(f"Bot is online! {bot.user}")
    save_all_messages_to_db_task.start()
    try:
        synced = await bot.tree.sync()
        logging.info(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        logging.error(f"Error syncing slash commands: {e}")

########################
# メッセージ履歴同期タスク
########################
@tasks.loop(minutes=5)
async def save_all_messages_to_db_task():
    await save_all_messages_to_db()

async def save_all_messages_to_db():
    """
    メッセージをページングで取得し、DBに保存する。
    リアクション情報の保存を除外。
    """
    channel = bot.get_channel(THREAD_ID)
    if channel is None:
        logging.error("指定したTHREAD_IDのチャンネルが見つかりませんでした。")
        return

    all_messages = []
    last_msg = None
    batch_size = 50  # バッチサイズを小さめに
    try:
        while True:
            batch = []
            # 'before' にはメッセージオブジェクトを渡す
            async for msg in channel.history(limit=batch_size, before=last_msg):
                batch.append(msg)

            if not batch:
                break

            all_messages.extend(batch)

            # ページングするために、"last_msg" はメッセージオブジェクト
            last_msg = batch[-1]

            # API制限を回避するためのスリープ
            await asyncio.sleep(1.0)

        if all_messages:
            await bulk_save_messages_to_db(all_messages)
        logging.info(f"Saved total {len(all_messages)} messages to the database (paging).")

    except discord.HTTPException as e:
        logging.error(f"Error fetching message history in paging: {e}")

async def bulk_save_messages_to_db(messages):
    """
    メッセージの基本情報のみをデータベースに保存。
    リアクション情報の保存は行わない。
    """
    conn = get_db_connection()
    if not conn or not messages:
        return
    try:
        data = []
        for message in messages:
            data.append((message.id, message.channel.id, message.author.id, message.content))
            logging.debug(f"Bulk saving message_id={message.id} to DB without reactions.")
        
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO messages (message_id, thread_id, author_id, content)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (message_id) DO NOTHING
            """, data)
            conn.commit()

        logging.info(f"Bulk inserted {len(messages)} messages without reactions.")
    except Error as e:
        logging.error(f"Error during bulk insert: {e}")
    finally:
        release_db_connection(conn)

########################
# Bot起動
########################
if DISCORD_TOKEN:
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logging.error(f"Error starting the bot: {e}")
        if db_pool:
            db_pool.closeall()
            logging.info("Closed all database connections.")
else:
    logging.error("DISCORD_TOKEN が設定されていません。")
