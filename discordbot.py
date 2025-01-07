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
# （実際のカスタム絵文字IDを必ず再確認してください）
########################
REACTIONS = {
    "b431": 1289782471197458495,  # <:b431:1289782471197458495>
    "b434": 1304690617405669376,  # <:b434:1304690617405669376>
    "b435": 1304690627723657267,  # <:b435:1304690627723657267>
}

READ_LATER_REACTION_ID = REACTIONS["b434"]     # あとで読む
FAVORITE_REACTION_ID   = REACTIONS["b435"]     # お気に入り
RANDOM_EXCLUDE_ID      = REACTIONS["b431"]     # ランダム除外
SPECIFIC_EXCLUDE_USER  = 695096014482440244    # 特定投稿者（例）

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
# 連続投稿者除外のための
# last_chosen_authors 辞書を定義
########################
last_chosen_authors = {}

########################
# ヘルパー関数
########################
async def safe_fetch_message(channel, message_id):
    try:
        return await channel.fetch_message(message_id)
    except (discord.NotFound, discord.HTTPException):
        return None

async def ensure_message_in_db(message):
    """
    メッセージがDBに存在しなければ新規挿入する。
    既存なら何もしない。
    """
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT id FROM messages WHERE message_id = %s", (message.id,))
            row = cur.fetchone()
            if row:
                return  # 既に存在するので挿入しない

            # リアクションを取得してJSONに変換
            reactions_dict = {}
            for reaction in message.reactions:
                if reaction.custom_emoji:
                    emoji_id = reaction.emoji.id
                    if emoji_id:
                        users = [user.id async for user in reaction.users()]
                        reactions_dict[str(emoji_id)] = users
            reactions_json = json.dumps(reactions_dict)

            cur.execute("""
                INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """,
            (message.id, message.channel.id, message.author.id, reactions_json, message.content))
            conn.commit()
            logging.info(f"Inserted new message into DB (message_id={message.id}).")

    except Error as e:
        logging.error(f"Error ensuring message in DB: {e}")
    finally:
        release_db_connection(conn)

async def update_reactions_in_db(message_id, emoji_id, user_id, add=True):
    """
    リアクションの追加・削除をDBに反映する
    """
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
    except Error as e:
        logging.error(f"Error updating reactions in DB: {e}")
    finally:
        release_db_connection(conn)

def user_reacted(msg, reaction_id, user_id):
    """
    DBから取得した messages テーブルの行(msg)に対して、
    指定の reaction_id を user_id が付けているかどうかを判定。
    """
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
    """
    指定スレッド(thread_id)のメッセージからランダムで1つを選ぶ。
    filter_func が指定されていれば、それを通ったメッセージのみを対象にする。
    """
    conn = get_db_connection()
    if not conn:
        return None

    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT * FROM messages WHERE thread_id = %s", (thread_id,))
            all_rows = cur.fetchall()

            # JSONパース
            for m in all_rows:
                if m['reactions'] is None:
                    m['reactions'] = {}
                elif isinstance(m['reactions'], str):
                    try:
                        m['reactions'] = json.loads(m['reactions']) or {}
                    except json.JSONDecodeError:
                        m['reactions'] = {}

            logging.info(f"[DEBUG] [{button_name}] get_random_message: total {len(all_rows)} messages before filter.")

            if filter_func:
                filtered = []
                for row in all_rows:
                    if filter_func(row):
                        filtered.append(row)
                logging.info(f"[DEBUG] [{button_name}] get_random_message: after filter -> {len(filtered)} messages remain.")
                all_rows = filtered

            if not all_rows:
                return None

            return random.choice(all_rows)

    except Error as e:
        logging.error(f"Error fetching random message: {e}")
        return None
    finally:
        release_db_connection(conn)

########################
# View クラス
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
        try:
            if random_message:
                # 選ばれたメッセージの投稿者を記録 (連続投稿者除外したい場合などに使う)
                last_chosen_authors[user_id] = random_message['author_id']

                author_name = await self.get_author_name(random_message['author_id'])
                await interaction.response.send_message(
                    f"{interaction.user.mention} さんには、{author_name} さんの投稿がおすすめです！\n"
                    f"https://discord.com/channels/{interaction.guild_id}/{THREAD_ID}/{random_message['message_id']}"
                )
            else:
                await interaction.response.send_message(
                    f"{interaction.user.mention} さん、該当する投稿が見つかりませんでした。\n"
                    "フィルター条件に一致する投稿がなかった可能性があります。",
                    ephemeral=True
                )
        except Exception as e:
            logging.error(f"Error handling selection: {e}")
            await interaction.response.send_message(
                f"エラーが発生しました。しばらくしてから再度お試しください。\n詳細: {e}",
                ephemeral=True
            )
        finally:
            # パネルを再送して常に最新の状態に
            await send_panel(interaction.channel)

    async def get_and_handle_random_message(self, interaction, filter_func, button_name="N/A"):
        random_msg = await get_random_message(THREAD_ID, filter_func=filter_func, button_name=button_name)
        await self.handle_selection(interaction, random_msg, interaction.user.id)

    # --- 青ボタン：ランダム
    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.primary, row=0, custom_id="blue_random")
    async def blue_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "blue_random"

        def filter_func(msg):
            # 自分の投稿を除外
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

    # --- 青ボタン：あとで読む (b434)
    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.primary, row=0, custom_id="read_later")
    async def read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "blue_read_later"

        def filter_func(msg):
            # b434 を付けた投稿のみ
            if not user_reacted(msg, READ_LATER_REACTION_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: no b434 from user.")
                return False
            # 自分の投稿除外
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

    # --- 青ボタン：お気に入り (b435)
    @discord.ui.button(label="お気に入り", style=discord.ButtonStyle.primary, row=0, custom_id="favorite")
    async def favorite(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "blue_favorite"

        def filter_func(msg):
            logging.debug(f"DB reactions for msg_id={msg['message_id']}: {msg['reactions']}")
            if not user_reacted(msg, FAVORITE_REACTION_ID, interaction.user.id):
                logging.debug(
                    f"Excluding msg_id={msg['message_id']}: reaction check failed, "
                    f"FAVORITE_REACTION_ID={FAVORITE_REACTION_ID}, "
                    f"user_id={interaction.user.id}, reactions={msg['reactions']}"
                )
                return False
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

    # --- 赤ボタン：ランダム (b431) 除外
    @discord.ui.button(label="ランダム", style=discord.ButtonStyle.danger, row=1, custom_id="red_random")
    async def red_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "red_random"

        def filter_func(msg):
            # b431(除外) を付けた投稿はスキップ
            if user_reacted(msg, RANDOM_EXCLUDE_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: user has b431.")
                return False
            # 自分の投稿除外
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            # 特定投稿者除外
            if msg['author_id'] == SPECIFIC_EXCLUDE_USER:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: specific exclude author.")
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)

    # --- 赤ボタン：あとで読む (b434) かつ b431除外
    @discord.ui.button(label="あとで読む", style=discord.ButtonStyle.danger, row=1, custom_id="conditional_read_later")
    async def conditional_read_later(self, interaction: discord.Interaction, button: discord.ui.Button):
        button_name = "red_read_later"

        def filter_func(msg):
            # b434 を付けた投稿のみ
            if not user_reacted(msg, READ_LATER_REACTION_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: no b434 from user.")
                return False
            # b431 が付いている投稿は除外
            if user_reacted(msg, RANDOM_EXCLUDE_ID, interaction.user.id):
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: user has b431.")
                return False
            # 自分の投稿除外
            if msg['author_id'] == interaction.user.id:
                logging.debug(f"[{button_name}] Excluding msg_id={msg['message_id']}: same user.")
                return False
            return True

        await self.get_and_handle_random_message(interaction, filter_func, button_name=button_name)


current_panel_message_id = None

async def send_panel(channel):
    """
    パネルメッセージを再送信し、以前のパネルは削除する
    """
    global current_panel_message_id
    if current_panel_message_id:
        try:
            panel_msg = await channel.fetch_message(current_panel_message_id)
            await panel_msg.delete()
            logging.info(f"Deleted previous panel message with ID {current_panel_message_id}.")
        except discord.NotFound:
            logging.warning(f"Previous panel message with ID {current_panel_message_id} not found.")
        except discord.HTTPException as e:
            logging.error(f"Error deleting panel message: {e}")

    embed = create_panel_embed()
    view = CombinedView()

    try:
        sent_message = await channel.send(embed=embed, view=view)
        current_panel_message_id = sent_message.id
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
            "**ランダム**：全体からランダム\n"
            "**あとで読む**： <:b434:1304690617405669376> を付けた投稿から選ぶ\n"
            "**お気に入り**： <:b435:1304690627723657267> を付けた投稿から選ぶ"
        ),
        color=0xFF69B4
    )
    return embed

########################
# スラッシュコマンド
########################
@bot.tree.command(name="panel", description="ルーレット用の操作パネルを表示します。")
async def panel(interaction: discord.Interaction):
    channel = interaction.channel
    if channel:
        await interaction.response.send_message("パネルを表示します！", ephemeral=True)
        await send_panel(channel)
    else:
        await interaction.response.send_message("エラー: チャンネルが取得できませんでした。", ephemeral=True)

@bot.tree.command(name="check_reactions", description="特定のメッセージのリアクションをDBで確認します。")
async def check_reactions(interaction: discord.Interaction, message_id: str):
    try:
        msg_id = int(message_id)
    except ValueError:
        await interaction.response.send_message("メッセージIDは数値を指定してください。", ephemeral=True)
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
                await interaction.response.send_message("DBに該当メッセージが存在しません。", ephemeral=True)
                return

            r = row['reactions'] or {}
            if isinstance(r, str):
                try:
                    r = json.loads(r)
                except json.JSONDecodeError:
                    r = {}

            logging.debug(f"/check_reactions for message_id={msg_id} -> {r}")

            if not r:
                await interaction.response.send_message("このメッセージにはリアクションがありません。", ephemeral=True)
            else:
                embed = discord.Embed(
                    title=f"Message ID: {msg_id} のリアクション情報",
                    color=0x00FF00
                )
                for emoji_id, user_ids in r.items():
                    # 絵文字オブジェクトを取得
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
        await interaction.response.send_message("リアクション情報の取得中にエラーが発生しました。", ephemeral=True)
    finally:
        release_db_connection(conn)

########################
# リアクションイベント
########################
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """
    ユーザーがリアクションを追加したときに発火。
    """
    logging.info(f"on_raw_reaction_add fired: emoji={payload.emoji}, user_id={payload.user_id}, message_id={payload.message_id}")

    # Bot自身のリアクションは無視
    if payload.user_id == bot.user.id:
        logging.debug("Reaction added by the bot itself; ignoring.")
        return

    # 対象絵文字かどうかを確認 (b431, b434, b435 など)
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

    # DBにメッセージがなければ挿入しておく
    await ensure_message_in_db(message)

    # DBにリアクションを保存
    await update_reactions_in_db(payload.message_id, payload.emoji.id, payload.user_id, add=True)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    """
    ユーザーがリアクションを削除したときに発火。
    """
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

    # DBにメッセージがなければ挿入しておく
    await ensure_message_in_db(message)

    # DBからリアクション削除
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
@tasks.loop(minutes=60)
async def save_all_messages_to_db_task():
    await save_all_messages_to_db()

async def save_all_messages_to_db():
    """
    指定したTHREAD_IDのチャンネルの最近のメッセージを取得し、
    DBに反映する
    """
    channel = bot.get_channel(THREAD_ID)
    if channel is None:
        logging.error("指定した THREAD_ID のチャンネルが見つかりませんでした。")
        return

    try:
        limit_count = 200
        messages = []
        async for msg in channel.history(limit=limit_count):
            messages.append(msg)
        if messages:
            await bulk_save_messages_to_db(messages)
        logging.info(f"Saved up to {limit_count} messages to the database.")
    except discord.HTTPException as e:
        logging.error(f"Error fetching message history: {e}")

async def bulk_save_messages_to_db(messages):
    """
    複数メッセージをまとめてDBに登録/更新する
    """
    conn = get_db_connection()
    if not conn or not messages:
        return
    try:
        data = []
        for message in messages:
            # リアクションを取得して JSON化
            reactions_dict = {}
            for reaction in message.reactions:
                if reaction.custom_emoji:
                    emoji_id = reaction.emoji.id
                    if emoji_id:
                        users = [user.id async for user in reaction.users()]
                        reactions_dict[str(emoji_id)] = users

            reactions_json = json.dumps(reactions_dict)
            data.append((message.id, message.channel.id, message.author.id, reactions_json, message.content))

        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO messages (message_id, thread_id, author_id, reactions, content)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (message_id) DO UPDATE SET
                  content = EXCLUDED.content,
                  reactions = EXCLUDED.reactions
            """, data)
            conn.commit()

        logging.info(f"Bulk inserted or updated {len(messages)} messages.")
    except Error as e:
        logging.error(f"Error during bulk insert/update: {e}")
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
