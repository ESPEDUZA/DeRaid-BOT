import os
import asyncio
from aiogram import Bot, Dispatcher, types
from collections import deque
import tweepy
from dotenv import load_dotenv
from aiohttp import ServerDisconnectedError

# Load environment variables from .env file
load_dotenv()

API_TOKEN = os.getenv('API_TOKEN')
BEARER_TOKEN = os.getenv('BEARER_TOKEN')

# Initialize the Tweepy client with OAuth2 Bearer Token
twitter_client = tweepy.Client(bearer_token=BEARER_TOKEN)

# Initialize bot and dispatcher
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

raid_queue = deque()  # Using deque for efficient queue management
ongoing_raid = None

BANNER_IMAGE_PATH = "2024-08-21 17.21.47.jpg"  # Update this with your actual image path or URL

async def send_message_with_deletion(chat_id: int, text: str, delay: int, parse_mode=None):
    """Helper function to send a message and delete it after a delay."""
    message = await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
    await asyncio.sleep(delay)
    await bot.delete_message(chat_id=chat_id, message_id=message.message_id)

async def send_message_with_banner(chat_id: int, text: str, delay: int, parse_mode=None):
    """Helper function to send a message with a banner image and delete it after a delay."""
    if BANNER_IMAGE_PATH.startswith("http://") or BANNER_IMAGE_PATH.startswith("https://"):
        message = await bot.send_photo(chat_id=chat_id, photo=BANNER_IMAGE_PATH, caption=text, parse_mode=parse_mode)
    else:
        if os.path.exists(BANNER_IMAGE_PATH):
            with open(BANNER_IMAGE_PATH, 'rb') as banner:
                message = await bot.send_photo(chat_id=chat_id, photo=banner, caption=text, parse_mode=parse_mode)
        else:
            message = await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
    await asyncio.sleep(delay)
    await bot.delete_message(chat_id=chat_id, message_id=message.message_id)

@dp.message_handler(commands=['start', 'help'])
async def send_welcome(message: types.Message):
    await send_message_with_banner(message.chat.id, "Welcome to D.RaidBot! Use /raid to start raiding X posts.", 60)

@dp.message_handler(commands=['raid'])
async def raid_command(message: types.Message):
    global ongoing_raid
    args = message.text.split()

    if len(args) < 6:
        await send_message_with_banner(message.chat.id, "Usage: /raid <post link> <likes> <retweets> <replies> <bookmarks>", 60)
        return

    post_link = args[1]
    likes_goal, retweets_goal, replies_goal, bookmarks_goal = map(int, args[2:6])

    if ongoing_raid:
        raid_queue.append({
            "post_link": post_link,
            "likes_goal": likes_goal,
            "retweets_goal": retweets_goal,
            "replies_goal": replies_goal,
            "bookmarks_goal": bookmarks_goal,
            "message": message  # Store the message object for use when this raid starts
        })
        await send_message_with_banner(message.chat.id, "A raid is already ongoing. Your raid has been queued.", 20)
    else:
        ongoing_raid = {
            "post_link": post_link,
            "likes_goal": likes_goal,
            "retweets_goal": retweets_goal,
            "replies_goal": replies_goal,
            "bookmarks_goal": bookmarks_goal,
            "likes": 0,
            "retweets": 0,
            "replies": 0,
            "bookmarks": 0,
            "message": message  # Store the message object for use in updates
        }
        await send_full_raid_update(message, ongoing_raid)
        await send_message_with_deletion(message.chat.id, f"Raid started for {post_link}. Goals: {likes_goal} likes, {retweets_goal} retweets, {replies_goal} replies, {bookmarks_goal} bookmarks.", 3)

@dp.message_handler(commands=['cancel'])
async def cancel_raid(message: types.Message):
    global ongoing_raid
    if ongoing_raid:
        await send_message_with_banner(message.chat.id, "The current raid has been canceled.", 60)
        ongoing_raid = None
        if raid_queue:
            next_raid = raid_queue.popleft()  # Use popleft() to efficiently pop from the deque
            ongoing_raid = {
                "post_link": next_raid['post_link'],
                "likes_goal": next_raid['likes_goal'],
                "retweets_goal": next_raid['retweets_goal'],
                "replies_goal": next_raid['replies_goal'],
                "bookmarks_goal": next_raid['bookmarks_goal'],
                "likes": 0,
                "retweets": 0,
                "replies": 0,
                "bookmarks": 0,
                "message": next_raid['message']  # Pass the correct message object
            }
            await send_full_raid_update(next_raid['message'], ongoing_raid)
            await send_message_with_deletion(ongoing_raid['message'].chat.id, f"Next raid started for {ongoing_raid['post_link']}", 3)
    else:
        await send_message_with_banner(message.chat.id, "No ongoing raid to cancel.", 60)

@dp.message_handler(commands=['queue'])
async def queue_status(message: types.Message):
    global ongoing_raid, raid_queue
    if not ongoing_raid and not raid_queue:
        await send_message_with_banner(message.chat.id, "No ongoing or queued raids at the moment.", 20)
    else:
        status = "📝 *Raid Queue Status*\n\n"
        if ongoing_raid:
            status += f"🔴 Current Raid: [Link]({ongoing_raid['post_link']})\n"
            status += f"❤️ {ongoing_raid['likes']}/{ongoing_raid['likes_goal']}   "
            status += f"🔁 {ongoing_raid['retweets']}/{ongoing_raid['retweets_goal']}   "
            status += f"💬 {ongoing_raid['replies']}/{ongoing_raid['replies_goal']}   "
            status += f"🔖 {ongoing_raid['bookmarks']}/{ongoing_raid['bookmarks_goal']}\n\n"
        else:
            status += "No ongoing raid.\n\n"

        if raid_queue:
            status += "🟡 Queued Raids:\n"
            for i, raid in enumerate(raid_queue, 1):
                status += f"{i}. [Link]({raid['post_link']}) - Goals: 👍 {raid['likes_goal']}, 🔁 {raid['retweets_goal']}, 💬 {raid['replies_goal']}, 🔖 {raid['bookmarks_goal']}\n"

        await send_message_with_banner(message.chat.id, status, 20, parse_mode="Markdown")

@dp.message_handler(commands=['status'])
async def raid_status(message: types.Message):
    global ongoing_raid
    if not ongoing_raid:
        await send_message_with_banner(message.chat.id, "No ongoing raid at the moment.", 60)
        return

    post_link = ongoing_raid['post_link']
    post_id = extract_post_id(post_link)

    try:
        # Fetch tweet metrics
        response = twitter_client.get_tweet(id=post_id, tweet_fields=['public_metrics'])
        metrics = response.data['public_metrics']

        current_likes = metrics.get('like_count', 0)
        current_retweets = metrics.get('retweet_count', 0)
        current_replies = metrics.get('reply_count', 0)
        current_bookmarks = metrics.get('bookmark_count', 0)

        # Calculate progress toward goals
        ongoing_raid['likes'] = current_likes
        ongoing_raid['retweets'] = current_retweets
        ongoing_raid['replies'] = current_replies
        ongoing_raid['bookmarks'] = current_bookmarks

        status_message = (
            f"🔻DeGod mode here please!🔻\n\n"
            f"▶️▶️[SMASH the tweet!]({post_link})◀️◀️\n\n"
            f"❤️ {current_likes}/{ongoing_raid['likes_goal']}   "
            f"🔁 {current_retweets}/{ongoing_raid['retweets_goal']}   "
            f"💬 {current_replies}/{ongoing_raid['replies_goal']}   "
            f"🔖 {current_bookmarks}/{ongoing_raid['bookmarks_goal']}"
        )

        await send_message_with_banner(message.chat.id, status_message, 60, parse_mode="Markdown")

    except tweepy.errors.TweepyException as e:
        print(f"Error fetching tweet metrics: {str(e)}")
        await send_message_with_banner(message.chat.id, f"Error fetching tweet metrics: {str(e)}", 60)
    except ServerDisconnectedError:
        await send_message_with_banner(message.chat.id, "Server disconnected. Please try again.", 60)
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        await send_message_with_banner(message.chat.id, f"Unexpected error: {str(e)}", 60)


async def track_engagement():
    global ongoing_raid
    while True:
        if ongoing_raid:
            post_link = ongoing_raid['post_link']
            post_id = extract_post_id(post_link)

            try:
                metrics = twitter_client.get_tweet(id=post_id, tweet_fields=['public_metrics']).data['public_metrics']

                current_likes = metrics['like_count']
                current_retweets = metrics['retweet_count']
                current_replies = metrics['reply_count']
                current_bookmarks = metrics['bookmark_count']

                ongoing_raid['likes'] = current_likes
                ongoing_raid['retweets'] = current_retweets
                ongoing_raid['replies'] = current_replies
                ongoing_raid['bookmarks'] = current_bookmarks

                if (current_likes >= ongoing_raid['likes_goal'] and
                        current_retweets >= ongoing_raid['retweets_goal'] and
                        current_replies >= ongoing_raid['replies_goal'] and
                        current_bookmarks >= ongoing_raid['bookmarks_goal']):
                    alert_message = (
                        f" 🟢🟢🟢*Raid Complete!* 🟢🟢🟢\n\n"
                        f"The raid on [this tweet]({post_link}) has successfully reached all its goals!\n\n"
                        f"❤️ {current_likes}/{ongoing_raid['likes_goal']}   "
                        f"🔁 {current_retweets}/{ongoing_raid['retweets_goal']}   "
                        f"💬 {current_replies}/{ongoing_raid['replies_goal']}   "
                        f"🔖 {current_bookmarks}/{ongoing_raid['bookmarks_goal']}\n\n"
                        f"FIWB you went full Degod mode on this!"
                    )
                    # Send raid completion message with a 15-second delay before deletion
                    await send_message_with_banner(
                        chat_id=ongoing_raid['message'].chat.id,
                        text=alert_message,
                        delay=10,
                        parse_mode="Markdown"
                    )

                    ongoing_raid = None

                    if raid_queue:
                        next_raid = raid_queue.popleft()
                        ongoing_raid = {
                            "post_link": next_raid['post_link'],
                            "likes_goal": next_raid['likes_goal'],
                            "retweets_goal": next_raid['retweets_goal'],
                            "replies_goal": next_raid['replies_goal'],
                            "bookmarks_goal": next_raid['bookmarks_goal'],
                            "likes": 0,
                            "retweets": 0,
                            "replies": 0,
                            "bookmarks": 0,
                            "message": next_raid['message']  # Pass the correct message object
                        }
                        await send_full_raid_update(ongoing_raid['message'], ongoing_raid)
                        await send_message_with_deletion(ongoing_raid['message'].chat.id,
                                                         f"Next raid started for {ongoing_raid['post_link']}", 3)
                else:
                    # Display the status automatically if the raid is not complete
                    await raid_status(ongoing_raid['message'])

            except ServerDisconnectedError:
                print("Server disconnected. Retrying...")
            except Exception as e:
                print(f"Unexpected error during tracking: {str(e)}")

        await asyncio.sleep(50)  # Check every 50 seconds


def extract_post_id(post_link):
    return post_link.split('/')[-1]

async def send_full_raid_update(message: types.Message, raid_data):
    interaction_text = (
        f"  🔥🔥🔥 *Join the Raid!* 🔥🔥🔥 \n\n"
        f"Enter Degod mode and *SMASH* this tweet!\n\n"
        f"❤️ {raid_data['likes']} | {raid_data['likes_goal']}   "
        f"🔁 {raid_data['retweets']} | {raid_data['retweets_goal']}   "
        f"💬 {raid_data['replies']} | {raid_data['replies_goal']}   "
        f"🔖 {raid_data['bookmarks']} | {raid_data['bookmarks_goal']}\n\n"
        f"▶️[Click here to raid the tweet!]({raid_data['post_link']})◀️"
    )
    await send_message_with_banner(message.chat.id, interaction_text, 60, parse_mode="Markdown")

async def main():
    asyncio.create_task(track_engagement())
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
