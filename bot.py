import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineQuery, InputTextMessageContent, InlineQueryResultArticle
from collections import deque
import tweepy
from dotenv import load_dotenv
from aiohttp import ServerDisconnectedError
from datetime import datetime, timedelta

# Load environment variables from .env file
load_dotenv()
#init staging
#test2
API_TOKEN = os.getenv('API_TOKEN')
BEARER_TOKEN = os.getenv('BEARER_TOKEN')

# Initialize the Tweepy client with OAuth2 Bearer Token
twitter_client = tweepy.Client(bearer_token=BEARER_TOKEN)

# Initialize bot and dispatcher
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

raid_queue = deque()  # Using deque for efficient queue management
ongoing_raid = None
stop_engagement_task = False  # Flag to stop engagement tracking
raid_start_time = None  # To track the start time of the raid
queue_enabled = False  # Flag to enable/disable queue system

BANNER_IMAGE_PATH = "1Green.mp4"  # Update this with your actual image path or URL


def get_color_for_completion(percentage):
    if percentage >= 100:
        return "🟩"  # Green for 100% completion
    elif percentage >= 75:
        return "🟨"  # Yellow for 75% to 99%
    elif percentage >= 50:
        return "🟧"  # Orange for 50% to 74%
    elif percentage > 0:
        return "🟥"  # Red for 1% to 49%
    else:
        return "🟥"  # Red for 0%


def create_text_progress_bar(percentage):
    """Create a text-based progress bar with custom symbols."""
    total_blocks = 10  # Total length of the progress bar
    filled_blocks = int(total_blocks * (percentage / 100))
    empty_blocks = total_blocks - filled_blocks

    # Create the progress bar using the provided symbols
    progress_bar = "◖" + "▮" * filled_blocks + "▯" * empty_blocks + "◗"
    return progress_bar


def format_duration(seconds):
    """Format the duration in seconds to a more readable string."""
    minutes, seconds = divmod(seconds, 60)
    if minutes > 0:
        return f"{minutes} minutes and {seconds} seconds"
    else:
        return f"{seconds} seconds"


async def send_message_with_deletion(chat_id: int, text: str, delay: int, parse_mode=None):
    """Helper function to send a message and delete it after a delay."""
    message = await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
    await asyncio.sleep(delay)
    await bot.delete_message(chat_id=chat_id, message_id=message.message_id)


async def is_admin(message: types.Message):
    """Check if the user is an admin."""
    chat_member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    return chat_member.is_chat_admin()


async def admin_only(message: types.Message, handler):
    """Allow only group admins to use this command."""
    if await is_admin(message):
        await handler(message)
    else:
        await send_message_with_deletion(message.chat.id, "This command is only available to group admins.", 10)


@dp.message_handler(commands=['start', 'help'])
async def send_welcome(message: types.Message):
    await admin_only(message, send_welcome_handler)


async def send_welcome_handler(message: types.Message):
    help_text = (
        "Welcome to D.RaidBot! Here are the commands you can use:\n\n"
        "/start or /help - Display this help message.\n\n"
        "/raid <post link> <likes> <retweets> <replies> <bookmarks> - Start a new raid on a specific tweet. "
        "You must provide the link to the tweet and the goals for likes, retweets, replies, and bookmarks.\n\n"
        "/cancel - Cancel the current ongoing raid.\n\n"
        "/cancelall - Cancel all ongoing and queued raids.\n\n"
        "/queueon - Enable the raid queue system.\n\n"
        "/queueoff - Disable the raid queue system.\n\n"
        "/queue - Display the status of the current raid and the list of queued raids.\n\n"
        "/status - Check the current status of the ongoing raid.\n\n"
    )
    await send_message_with_deletion(message.chat.id, help_text, 60)


@dp.message_handler(commands=['raid'])
async def raid_command(message: types.Message):
    await admin_only(message, raid_command_handler)


async def raid_command_handler(message: types.Message):
    global ongoing_raid, stop_engagement_task, raid_start_time
    args = message.text.split()

    if len(args) < 6:
        await bot.send_message(message.chat.id,
                               "Usage: /raid <post link> <likes> <retweets> <replies> <bookmarks>")
        return

    post_link = args[1]
    new_likes_goal, new_retweets_goal, new_replies_goal, new_bookmarks_goal = map(int, args[2:6])

    post_id = extract_post_id(post_link)

    try:
        # Fetch the current engagement metrics using the correct fields
        response = twitter_client.get_tweet(id=post_id, tweet_fields=['public_metrics'])
        metrics = response.data['public_metrics']

        current_likes = metrics.get('like_count', 0)
        current_retweets = metrics.get('retweet_count', 0)
        current_replies = metrics.get('reply_count', 0)
        current_bookmarks = metrics.get('bookmark_count', 0)

        if ongoing_raid:
            if queue_enabled:
                raid_queue.append({
                    "post_link": post_link,
                    "likes_goal": new_likes_goal,
                    "retweets_goal": new_retweets_goal,
                    "replies_goal": new_replies_goal,
                    "bookmarks_goal": new_bookmarks_goal,
                    "message": message  # Store the message object for use when this raid starts
                })
                await bot.send_message(message.chat.id, "A raid is already ongoing. Your raid has been queued.")
            else:
                await bot.send_message(message.chat.id, "A raid is already ongoing. Queueing is currently disabled.")
        else:
            stop_engagement_task = False  # Reset the flag for a new raid
            raid_start_time = datetime.utcnow()  # Start tracking the time for the raid
            ongoing_raid = {
                "post_link": post_link,
                "likes_goal": new_likes_goal,
                "retweets_goal": new_retweets_goal,
                "replies_goal": new_replies_goal,
                "bookmarks_goal": new_bookmarks_goal,
                "initial_likes": current_likes,
                "initial_retweets": current_retweets,
                "initial_replies": current_replies,
                "initial_bookmarks": current_bookmarks,
                "likes": current_likes,
                "retweets": current_retweets,
                "replies": current_replies,
                "bookmarks": current_bookmarks,
                "message": message  # Store the message object for use in updates
            }
            # Ensure initial progress is 0%
            pinned_message = await send_full_raid_update(message, ongoing_raid, initial=True)
            await bot.pin_chat_message(chat_id=message.chat.id, message_id=pinned_message.message_id,
                                       disable_notification=True)
            # Store the pinned message ID in ongoing_raid for deletion later
            ongoing_raid['pinned_message_id'] = pinned_message.message_id

    except tweepy.errors.TweepyException as e:
        await bot.send_message(message.chat.id, f"Error fetching tweet metrics: {str(e)}")
    except Exception as e:
        await bot.send_message(message.chat.id, f"Unexpected error: {str(e)}")


@dp.message_handler(commands=['cancel'])
async def cancel_raid(message: types.Message):
    await admin_only(message, cancel_raid_handler)


async def cancel_raid_handler(message: types.Message):
    global ongoing_raid, stop_engagement_task
    if ongoing_raid:
        stop_engagement_task = True  # Signal to stop engagement tracking
        await bot.send_message(message.chat.id, "The current raid has been canceled.")
        await cleanup_tracking_messages(ongoing_raid['message'].chat.id, delay=0)  # Set delay to 0 for instant cleanup
        ongoing_raid = None

        if queue_enabled and raid_queue:
            next_raid = raid_queue.popleft()  # Use popleft() to efficiently pop from the deque
            stop_engagement_task = False  # Reset the flag for the next raid
            ongoing_raid = {
                "post_link": next_raid['post_link'],
                "likes_goal": next_raid['likes_goal'],
                "retweets_goal": next_raid['retweets_goal'],
                "replies_goal": next_raid['replies_goal'],
                "bookmarks_goal": next_raid['bookmarks_goal'],
                "initial_likes": 0,
                "initial_retweets": 0,
                "initial_replies": 0,
                "initial_bookmarks": 0,
                "likes": 0,
                "retweets": 0,
                "replies": 0,
                "bookmarks": 0,
                "message": next_raid['message']  # Pass the correct message object
            }
            # Send and pin the full raid update message with progress at 0%
            pinned_message = await send_full_raid_update(next_raid['message'], ongoing_raid, initial=True)
            await bot.pin_chat_message(chat_id=ongoing_raid['message'].chat.id,
                                       message_id=pinned_message.message_id, disable_notification=True)
            ongoing_raid['pinned_message_id'] = pinned_message.message_id
    else:
        # Check if the message was queued and remove it
        removed = False
        for raid in raid_queue:
            if raid['message'].message_id == message.message_id:
                raid_queue.remove(raid)
                removed = True
                break

        if removed:
            await bot.send_message(message.chat.id, "The raid was in the queue and has been canceled.")
        else:
            await bot.send_message(message.chat.id, "No ongoing or queued raid to cancel.")


@dp.message_handler(commands=['cancelall'])
async def cancel_all_raids_handler(message: types.Message):
    global ongoing_raid, stop_engagement_task, raid_queue

    # Cancel the ongoing raid if there is one
    if ongoing_raid:
        stop_engagement_task = True
        await bot.send_message(message.chat.id, "All raids have been canceled.")
        await cleanup_tracking_messages(ongoing_raid['message'].chat.id, delay=60)
        ongoing_raid = None

    # Clear the queue
    if raid_queue:
        raid_queue.clear()

    await bot.send_message(message.chat.id, "The raid queue has been cleared.")


@dp.message_handler(commands=['queue'])
async def queue_status(message: types.Message):
    await admin_only(message, queue_status_handler)


async def queue_status_handler(message: types.Message):
    global ongoing_raid, raid_queue
    if not ongoing_raid and not raid_queue:
        await send_message_with_deletion(message.chat.id, "No ongoing or queued raids at the moment.", 20)
    else:
        status = "*UPCOMING RAIDS*\n\n"
        if queue_enabled and raid_queue:
            status += "\n"
            for i, raid in enumerate(raid_queue, 1):
                status += f"{i}. [Tweet]({raid['post_link']}) {raid['likes_goal']} {raid['retweets_goal']} {raid['replies_goal']} {raid['bookmarks_goal']}\n"
        else:
            status += "No upcoming raid.\n\n"
        await send_message_with_deletion(message.chat.id, status, 20, parse_mode="Markdown")

@dp.message_handler(commands=['status'])
async def raid_status(message: types.Message):
    await admin_only(message, raid_status_handler)


async def raid_status_handler(message: types.Message):
    global ongoing_raid
    if not ongoing_raid:
        await send_message_with_deletion(message.chat.id, "No ongoing raid at the moment.", 60)
        return

    post_link = ongoing_raid['post_link']
    post_id = extract_post_id(post_link)

    try:
        # Fetch tweet metrics
        response = twitter_client.get_tweet(id=post_id, tweet_fields='public_metrics')
        metrics = response.data['public_metrics']

        current_likes = metrics.get('like_count', 0)
        current_retweets = metrics.get('retweet_count', 0)
        current_replies = metrics.get('reply_count', 0)
        current_bookmarks = metrics.get('bookmark_count', 0)

        # Calculate progress toward goals relative to the start of the raid
        likes_progress = (current_likes - ongoing_raid['initial_likes']) / ongoing_raid['likes_goal'] * 100 if ongoing_raid['likes_goal'] > 0 else 100
        retweets_progress = (current_retweets - ongoing_raid['initial_retweets']) / ongoing_raid['retweets_goal'] * 100 if ongoing_raid['retweets_goal'] > 0 else 100
        replies_progress = (current_replies - ongoing_raid['initial_replies']) / ongoing_raid['replies_goal'] * 100 if ongoing_raid['replies_goal'] > 0 else 100
        bookmarks_progress = (current_bookmarks - ongoing_raid['initial_bookmarks']) / ongoing_raid['bookmarks_goal'] * 100 if ongoing_raid['bookmarks_goal'] > 0 else 100

        overall_progress = (likes_progress + retweets_progress + replies_progress + bookmarks_progress) / 4

        ongoing_raid['likes'] = current_likes
        ongoing_raid['retweets'] = current_retweets
        ongoing_raid['replies'] = current_replies
        ongoing_raid['bookmarks'] = current_bookmarks

        status_message = (
            f"\n*SMASH THE RAID OR PASTA DIES*\n"
            f"◖{'▮' * int(20 * (overall_progress / 100))}{'▯' * (20 - int(20 * (overall_progress / 100)))}◗\n\n"
            f"{get_color_for_completion(likes_progress)} Likes: {current_likes} of {ongoing_raid['initial_likes'] + ongoing_raid['likes_goal']}\n"
            f"{get_color_for_completion(retweets_progress)} Retweets: {current_retweets} of {ongoing_raid['initial_retweets'] + ongoing_raid['retweets_goal']}\n"
            f"{get_color_for_completion(replies_progress)} Replies: {current_replies} of {ongoing_raid['initial_replies'] + ongoing_raid['replies_goal']}\n"
            f"{get_color_for_completion(bookmarks_progress)} Bookmarks: {current_bookmarks} of {ongoing_raid['initial_bookmarks'] + ongoing_raid['bookmarks_goal']}\n\n"
            f"{post_link}"
        )

        await send_full_raid_update(message, ongoing_raid)

    except tweepy.errors.TweepyException as e:
        await send_message_with_deletion(message.chat.id, f"Error fetching tweet metrics: {str(e)}", 60)
    except ServerDisconnectedError:
        await send_message_with_deletion(message.chat.id, "Server disconnected. Please try again.", 60)
    except Exception as e:
        await send_message_with_deletion(message.chat.id, f"Unexpected error: {str(e)}", 60)


@dp.message_handler(commands=['queueon'])
async def enable_queue(message: types.Message):
    await admin_only(message, enable_queue_handler)


async def enable_queue_handler(message: types.Message):
    global queue_enabled
    queue_enabled = True
    await bot.send_message(message.chat.id, "Raid queueing has been enabled.")


@dp.message_handler(commands=['queueoff'])
async def disable_queue(message: types.Message):
    await admin_only(message, disable_queue_handler)


async def disable_queue_handler(message: types.Message):
    global queue_enabled
    queue_enabled = False
    await bot.send_message(message.chat.id, "Raid queueing has been disabled.")


@dp.inline_handler()
async def inline_query_handler(inline_query: InlineQuery):
    query = inline_query.query.lower()

    commands = [
        "queueon", "queueoff", "queue", "cancel", "cancelall", "raid", "status"
    ]
    results = []

    for cmd in commands:
        if cmd.startswith(query):
            results.append(
                InlineQueryResultArticle(
                    id=cmd,
                    title=f"/{cmd}",
                    input_message_content=InputTextMessageContent(f"/{cmd}")
                )
            )

    await bot.answer_inline_query(inline_query.id, results)


async def track_engagement():
    global ongoing_raid, stop_engagement_task, raid_start_time

    while True:
        if ongoing_raid:
            post_link = ongoing_raid['post_link']
            post_id = extract_post_id(post_link)

            try:
                metrics = twitter_client.get_tweet(id=post_id, tweet_fields='public_metrics').data['public_metrics']

                current_likes = metrics['like_count']
                current_retweets = metrics['retweet_count']
                current_replies = metrics['reply_count']
                current_bookmarks = metrics['bookmark_count']

                # Update the ongoing raid metrics
                ongoing_raid['likes'] = current_likes
                ongoing_raid['retweets'] = current_retweets
                ongoing_raid['replies'] = current_replies
                ongoing_raid['bookmarks'] = current_bookmarks

                # Calculate progress for each metric relative to the initial values
                likes_percentage = (current_likes - ongoing_raid['initial_likes']) / ongoing_raid['likes_goal'] * 100 if ongoing_raid['likes_goal'] > 0 else 100
                retweets_percentage = (current_retweets - ongoing_raid['initial_retweets']) / ongoing_raid['retweets_goal'] * 100 if ongoing_raid['retweets_goal'] > 0 else 100
                replies_percentage = (current_replies - ongoing_raid['initial_replies']) / ongoing_raid['replies_goal'] * 100 if ongoing_raid['replies_goal'] > 0 else 100
                bookmarks_percentage = (current_bookmarks - ongoing_raid['initial_bookmarks']) / ongoing_raid['bookmarks_goal'] * 100 if ongoing_raid['bookmarks_goal'] > 0 else 100

                # Stop sending updates if the raid has been canceled
                if stop_engagement_task:
                    return

                # Check if the raid has exceeded the 1-hour time limit
                if datetime.utcnow() - raid_start_time > timedelta(hours=1):
                    await bot.send_message(
                        chat_id=ongoing_raid['message'].chat.id,
                        text="Damn, we didn't smash that raid enough 😭😭",
                        parse_mode="Markdown",
                        reply_to_message_id=ongoing_raid['pinned_message_id']
                    )
                    await cleanup_tracking_messages(ongoing_raid['message'].chat.id, delay=60)
                    ongoing_raid = None

                    if queue_enabled and raid_queue:
                        next_raid = raid_queue.popleft()
                        stop_engagement_task = False  # Reset the flag for the next raid
                        raid_start_time = datetime.utcnow()  # Start tracking the time for the new raid
                        ongoing_raid = {
                            "post_link": next_raid['post_link'],
                            "likes_goal": next_raid['likes_goal'],
                            "retweets_goal": next_raid['retweets_goal'],
                            "replies_goal": next_raid['replies_goal'],
                            "bookmarks_goal": next_raid['bookmarks_goal'],
                            "initial_likes": 0,
                            "initial_retweets": 0,
                            "initial_replies": 0,
                            "initial_bookmarks": 0,
                            "likes": 0,
                            "retweets": 0,
                            "replies": 0,
                            "bookmarks": 0,
                            "message": next_raid['message']  # Pass the correct message object
                        }
                        pinned_message = await send_full_raid_update(ongoing_raid['message'], ongoing_raid, initial=True)
                        await bot.pin_chat_message(chat_id=ongoing_raid['message'].chat.id,
                                                   message_id=pinned_message.message_id, disable_notification=True)
                        ongoing_raid['pinned_message_id'] = pinned_message.message_id
                else:
                    # Determine the color for each metric
                    likes_color = get_color_for_completion(likes_percentage)
                    retweets_color = get_color_for_completion(retweets_percentage)
                    replies_color = get_color_for_completion(replies_percentage)
                    bookmarks_color = get_color_for_completion(bookmarks_percentage)

                    if (likes_percentage >= 100 and retweets_percentage >= 100 and
                            replies_percentage >= 100 and bookmarks_percentage >= 100):
                        raid_duration = datetime.utcnow() - raid_start_time  # Calculate raid duration
                        duration_str = format_duration(int(raid_duration.total_seconds()))  # Format the duration
                        alert_message = (
                            f"GJ BOYS WE FUCKED THAT RAID 🙏🙏\n\n"
                            f"COMPLETED IN JUST {duration_str}!! 😈😈"
                        )
                        # Send the final raid completion message as a reply to the pinned message
                        await bot.send_message(
                            chat_id=ongoing_raid['message'].chat.id,
                            text=alert_message,
                            parse_mode="Markdown",
                            reply_to_message_id=ongoing_raid['pinned_message_id']  # Reply to the last pinned message
                        )

                        await cleanup_tracking_messages(ongoing_raid['message'].chat.id, delay=60)
                        ongoing_raid = None

                        if queue_enabled and raid_queue:
                            next_raid = raid_queue.popleft()
                            stop_engagement_task = False  # Reset the flag for the next raid
                            raid_start_time = datetime.utcnow()  # Start tracking the time for the new raid
                            ongoing_raid = {
                                "post_link": next_raid['post_link'],
                                "likes_goal": next_raid['likes_goal'],
                                "retweets_goal": next_raid['retweets_goal'],
                                "replies_goal": next_raid['replies_goal'],
                                "bookmarks_goal": next_raid['bookmarks_goal'],
                                "initial_likes": 0,
                                "initial_retweets": 0,
                                "initial_replies": 0,
                                "initial_bookmarks": 0,
                                "likes": 0,
                                "retweets": 0,
                                "replies": 0,
                                "bookmarks": 0,
                                "message": next_raid['message']  # Pass the correct message object
                            }
                            pinned_message = await send_full_raid_update(ongoing_raid['message'], ongoing_raid, initial=True)
                            await bot.pin_chat_message(chat_id=ongoing_raid['message'].chat.id,
                                                       message_id=pinned_message.message_id, disable_notification=True)
                            ongoing_raid['pinned_message_id'] = pinned_message.message_id
                    else:
                        # Delete the previously pinned message before sending the next update
                        if 'pinned_message_id' in ongoing_raid and ongoing_raid['pinned_message_id']:
                            try:
                                await bot.delete_message(chat_id=ongoing_raid['message'].chat.id,
                                                         message_id=ongoing_raid['pinned_message_id'])
                            except Exception as e:
                                print(f"Error deleting pinned message: {str(e)}")

                        # Send the updated raid status
                        pinned_message = await send_full_raid_update(ongoing_raid['message'], ongoing_raid)

                        # Pin the new message
                        await bot.pin_chat_message(chat_id=ongoing_raid['message'].chat.id,
                                                   message_id=pinned_message.message_id,
                                                   disable_notification=True)

                        # Store the ID of the current message to delete it later
                        ongoing_raid['pinned_message_id'] = pinned_message.message_id

            except ServerDisconnectedError:
                print("Server disconnected. Retrying...")
            except Exception as e:
                print(f"Unexpected error during tracking: {str(e)}")

        await asyncio.sleep(65)  # Check every 65 seconds


def extract_post_id(post_link):
    return post_link.split('/')[-1]


async def send_full_raid_update(message: types.Message, raid_data, initial=False):
    likes_progress = 0 if initial else (raid_data['likes'] - raid_data['initial_likes'])
    retweets_progress = 0 if initial else (raid_data['retweets'] - raid_data['initial_retweets'])
    replies_progress = 0 if initial else (raid_data['replies'] - raid_data['initial_replies'])
    bookmarks_progress = 0 if initial else (raid_data['bookmarks'] - raid_data['initial_bookmarks'])

    likes_percentage = (likes_progress / raid_data['likes_goal'] * 100) if raid_data['likes_goal'] > 0 else 100
    retweets_percentage = (retweets_progress / raid_data['retweets_goal'] * 100) if raid_data['retweets_goal'] > 0 else 100
    replies_percentage = (replies_progress / raid_data['replies_goal'] * 100) if raid_data['replies_goal'] > 0 else 100
    bookmarks_percentage = (bookmarks_progress / raid_data['bookmarks_goal'] * 100) if raid_data['bookmarks_goal'] > 0 else 100

    # Determine the color for each metric
    likes_color = get_color_for_completion(likes_percentage)
    retweets_color = get_color_for_completion(retweets_percentage)
    replies_color = get_color_for_completion(replies_percentage)
    bookmarks_color = get_color_for_completion(bookmarks_percentage)

    # Create the progress bar (represented as text)
    total_blocks = 20
    valid_percentages = [
        likes_percentage if raid_data['likes_goal'] > 0 else None,
        retweets_percentage if raid_data['retweets_goal'] > 0 else None,
        replies_percentage if raid_data['replies_goal'] > 0 else None,
        bookmarks_percentage if raid_data['bookmarks_goal'] > 0 else None
    ]
    valid_percentages = [p for p in valid_percentages if p is not None]
    overall_progress = sum(valid_percentages) / len(valid_percentages) if valid_percentages else 100

    filled_blocks = int(total_blocks * (overall_progress / 100))
    empty_blocks = total_blocks - filled_blocks
    progress_bar = (
            "◖" + "▮" * filled_blocks + "▯" * empty_blocks + "◗"
    )

    interaction_text = (
        f"\n*SMASH THE RAID OR PASTA DIES*\n\n"
        f"{progress_bar}\n\n"
        f"{likes_color} Likes: {raid_data['likes']} of {raid_data['initial_likes'] + raid_data['likes_goal']}\n"
        f"{retweets_color} Retweets: {raid_data['retweets']} of {raid_data['initial_retweets'] + raid_data['retweets_goal']}\n"
        f"{replies_color} Replies: {raid_data['replies']} of {raid_data['initial_replies'] + raid_data['replies_goal']}\n"
        f"{bookmarks_color} Bookmarks: {raid_data['bookmarks']} of {raid_data['initial_bookmarks'] + raid_data['bookmarks_goal']}\n\n"
        f"{raid_data['post_link']}"
    )

    # Send the message with the banner
    if BANNER_IMAGE_PATH.startswith("http://") or BANNER_IMAGE_PATH.startswith("https://"):
        return await bot.send_photo(chat_id=message.chat.id, photo=BANNER_IMAGE_PATH, caption=interaction_text,
                                    parse_mode="Markdown")
    else:
        if os.path.exists(BANNER_IMAGE_PATH):
            with open(BANNER_IMAGE_PATH, 'rb') as banner:
                return await bot.send_photo(chat_id=message.chat.id, photo=banner, caption=interaction_text,
                                            parse_mode="Markdown")
        else:
            return await bot.send_message(chat_id=message.chat.id, text=interaction_text, parse_mode="Markdown")


async def cleanup_tracking_messages(chat_id: int, delay: int):
    """Deletes the pinned tracking message after a specified delay."""
    await asyncio.sleep(delay)
    try:
        chat = await bot.get_chat(chat_id)
        pinned_message = chat.pinned_message
        if pinned_message:
            await bot.delete_message(chat_id, pinned_message.message_id)
    except Exception as e:
        print(f"Error during cleanup of tracking messages: {str(e)}")


async def main():
    asyncio.create_task(track_engagement())
    await dp.start_polling()


if __name__ == "__main__":
    asyncio.run(main())
