#!/usr/bin/python3 
import sys
import praw
import prawcore
import time
import logging
import config
import dataset
from datetime import datetime 
from collections import deque

logger = logging.getLogger()
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter(
		'%(asctime)s %(levelname)-8s %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Reddit app API login creds
username = config.username
r = praw.Reddit(client_id=config.client_id, 
				client_secret=config.client_secret,
				user_agent=config.user_agent,
				username=config.username,
				password=config.password)

subname = config.subname
subreddit = r.subreddit(subname)

REMOVAL_PERIOD_MINUTES = 0
REINSTATE_PERIOD_MINUTES = 30
ANSWER_MINIMUM = 20
ANSWER_PHRASE_MINIMUM = 60
REMOVAL_PHRASES = []
POST_FETCH_LIMIT = 500
POST_DB_PRUNE_MINUTES = 1440
message_title = config.message_title
message_body = config.message_body
retry_message_title = config.retry_message_title
retry_message_body = config.retry_message_body
sticky_comment = config.sticky_comment
timeout_message_title = config.timeout_message_title
timeout_message_body = config.timeout_message_body
# Keep track of the last 1,000 posts and users to avoid reprocessing
post_history = deque([])
user_history = deque([])

# Database structure
# Table posts
# -----------
# --> id
# --> created
# --> user
# --> prompted
# --> removed
# --> replied
db = dataset.connect('sqlite:///questionbot.db')
post_table = db['posts']

# Load config values from AITP wiki, fall back to defaults
def load_config():
	global REMOVAL_PERIOD_MINUTES, REINSTATE_PERIOD_MINUTES, REMOVAL_PHRASES, ANSWER_MINIMUM, ANSWER_PHRASE_MINIMUM, POST_FETCH_LIMIT, POST_DB_PRUNE_MINUTES
	wikipage = r.subreddit("AmITheProblem").wiki["botconfig"]
	for line in wikipage.content_md.split("\n"):
		if "REMOVAL_PERIOD_MINUTES" in line.upper():
			REMOVAL_PERIOD_MINUTES = int(line.partition(":")[2].lstrip())
		elif "REINSTATE_PERIOD_MINUTES" in line.upper():
			REINSTATE_PERIOD_MINUTES = int(line.partition(":")[2].lstrip())
		elif "REMOVAL_PHRASES" in line.upper():
			phrase_string = line.partition(":")[2].lstrip().partition("[")[2].partition("]")[0]
			REMOVAL_PHRASES = list(phrase_string.replace(", ", ",").split(","))
		elif "ANSWER_MINIMUM" in line.upper():
			ANSWER_MINIMUM = int(line.partition(":")[2].lstrip())
		elif "ANSWER_PHRASE_MINIMUM" in line.upper():
			ANSWER_PHRASE_MINIMUM = int(line.partition(":")[2].lstrip())
		elif "POST_FETCH_LIMIT" in line.upper():
			POST_FETCH_LIMIT = int(line.partition(":")[2].lstrip())
		elif "POST_DB_PRUNE_MINUTES" in line.upper():
			POST_DB_PRUNE_MINUTES = int(line.partition(":")[2].lstrip())


# Calculates the age in hours of a reddit submission
def get_age(created_utc):
    t = datetime.now()
    utc_seconds = time.mktime(t.timetuple())
    minutes = round((utc_seconds - created_utc) / 60, 2)
    return minutes


# Determines whether bot user has already replied to a reddit submission or comment
def replied(item):
	replies = ""
	if "_replies" in vars(item).keys():
		replies = item.replies
	else:
		replies = item.comments
	for reply in replies:
		if reply.author and reply.author.name.lower() == username.lower():
			return True
	return False


# Return whether user has a post to the subreddit in the post DB
def has_approved_post(post_user):
	for i, user in enumerate(user_history):
		if user == post_user:
			post = r.submission(post_history[i])
			if post.approved:
				return True
	return False


# Check user history for a post created within the reinstatement period 
# Necessary if DB is missing a post
def user_has_recent_post(user):
	for post in user.new(limit=20):
		if post.subreddit == subreddit and get_age(post.created_utc) <= REINSTATE_PERIOD_MINUTES:
			return True
	return False


# Look up a reddit post in DB by ID
def get_post(submission):
	entry = post_table.find_one(post_id=submission.id)
	return entry


# Send user message requesting their answer
def prompt_user(submission):
	post = post_table.find_one(post_id=submission.id)
	# Only send to users we have not already messaged
	if not post['prompted']:
		user = submission.author
		prompt_message_body = message_body.replace("{{post}}", submission.shortlink)
		try:
			user.message(subject=message_title, message=prompt_message_body)
		except Exception as e:
			logging.error("Failed to message %s: %s" % (user.name, str(e)))

		post_table.update(dict(id=post['id'], prompted=True), ['id'])


# Send follow-up message
def retry_prompt_user(user):
	r.redditor(user).message(subject=retry_message_title, message=retry_message_body)


# Write user xp to database manually
def add_post(submission):
	try:
		insertion = post_table.insert(dict(
								   post_id=submission.id, 
								   user=submission.author.name, 
								   created=submission.created_utc,
								   prompted=False,
								   removed=False,
								   replied=False
							   ))
	except Exception as e:
		logging.erroor("Failed to add to DB: %s" % str(e))

	if not insertion:
		logging.error("Post failed to insert %s " % submission.shortlink)


# Delete post from DB
def db_delete_post(post):
	post_to_delete = post_table.find_one(post_id=post["post_id"])
	if not post_to_delete:
		return
	deleted = post_table.delete(id=post_to_delete['id'])
	if not deleted:
		logging.warning("Failed to delete %s" % post["post_id"])


# Remove user's old posts from DB when new post is posted
def db_clear_user_posts(user):
	user_post = post_table.find_one(user=user)
	if user_post:
		logging.info("Clearing DB for %s" % submission.author.name)
		post_table.delete(user=user)


# Remove posts over removal threshold
# Remove posts from DB over reinstatement threshold
def process_post_queue():
	for post in post_table:
		elapsed_time = get_age(post['created'])
		# Remove posts older than removal threshold
		if elapsed_time >= REMOVAL_PERIOD_MINUTES and not post['removed']:
			submission = r.submission(post['post_id'])
			logging.info("Removing %s (%s)" % (post['user'], submission.shortlink))
			submission.mod.remove()
			post_table.update(dict(id=post['id'], removed=True), ['id'])
		# Remove posts from DB older than prune threshold
		if elapsed_time >= POST_DB_PRUNE_MINUTES:
			logging.info("Deleting from DB %s (%s) (time limit reached: %s mins)" % (post['user'], post['post_id'], elapsed_time))
			db_delete_post(post)


# Leave the user answer as a stickied comment on the post
def post_user_answer(submission, answer):
	bot_comment = sticky_comment.replace("{{answer}}", answer)
	if not submission["replied"]:
		submission = r.submission(submission['post_id'])
		comment = submission.reply(bot_comment)
		comment.mod.distinguish(sticky=True)
		comment.mod.lock()


# Reinstate removed post from DB
def approve_post(post):
	submission = r.submission(post['post_id'])
	submission.mod.approve()
	# Add automod reports to the approved post
	if submission.num_reports > 0:
		submission.report("Warning: Check automod queue for auto report")


# Check PMs to process answers
def process_inbox():
	inbox = r.inbox.unread()
	for message in inbox:
		# Do not process if user deleted account
		if not message.author or "[deleted]" in message.author.name:
			continue

		user = message.author.name
		answer = message.body
		user_post = None
		user_post = post_table.find_one(user=user)
		message_age_minutes = get_age(message.created_utc)
		# Time delta is period between when post was created and when user answered the message
		message_time_delta = 999
		if user_post:
			message_time_delta = round(get_age(user_post['created']) - message_age_minutes, 2)

		# If user responds outside of window and post was not approved, send timeout message
		# First conditional set is for posts no longer in DB and checks the user history
		# Second conditional is for users with posts still in DB
		if (not user_post \
		and (message_title in message.subject or retry_message_title in message.subject) \
		and not has_approved_post(message.author) \
		and not user_has_recent_post(message.author)) \
		or (user_post \
		and message_time_delta >= REINSTATE_PERIOD_MINUTES \
		and not user_post['replied']):
			logstring = "Got timed out response from %s" % user
			if user_post:
				logstring += (" (%s mins)" % message_time_delta)
			logging.info(logstring)
			message.author.message(subject=timeout_message_title, message=timeout_message_body)
			r.inbox.mark_read([message])
			continue

		# Ignore messages not in reply to bot
		elif not user_post and not user_has_recent_post(message.author):
			logging.info("Got random message from %s" % user)
			r.inbox.mark_read([message])
			continue

		# User has no existing post, do nothing
		elif not user_post:
			logging.info("Cound not find post for %s" % user)
			r.inbox.mark_read([message])
			continue

		# Check for lazy answers
		if len(answer) < ANSWER_MINIMUM:
			logging.info("Got short message from %s, reprompting" % user)
			retry_prompt_user(user)

		# Check for lazy answers with unacceptable phrases
		elif len(answer) < ANSWER_PHRASE_MINIMUM:
			for phrase in REMOVAL_PHRASES:
				if phrase.lower() in answer.lower():
					logging.info("Got short message with phrase from %s, reprompting" % user)
					retry_prompt_user(user)
					break

		# Standard answer scenario			
		else:
			logging.info("Answer from %s (%s mins): %s..." % (user, message_time_delta, answer[:50]))
			if user_post['removed'] and message_time_delta <= REINSTATE_PERIOD_MINUTES:
				post_user_answer(user_post, answer)
				post_table.update(dict(id=user_post['id'], 
									   post_id=user_post["post_id"], 
									   replied=True), 
									   ['id'])
				logging.info("Approving %s (%s)" % (user, user_post["post_id"]))
				approve_post(user_post)
			logging.info("Deleting %s (%s) from DB (answer completed)" % (user, user_post["post_id"]))
			db_delete_post(user_post)

		r.inbox.mark_read([message])


# Print all rows in post database
def print_post_db():
	for post in post_table:
		print("%s %s" % (post['user'], post['post_id']))
		

# Delete all entries from post database
def clear_post_db():
	for post in post_table:
		r.submission(post['post_id']).mod.approve()
		db_delete_post(post)

# Main entry point
if __name__ == "__main__":
	try:
		load_config()
		logging.info("Loaded config successfully")
	except Exception as e:
		logging.error("Failed to load config, using default values: %s" % str(e))

	try:

		while True:

			# Fetch new submissions to subreddit
			for submission in subreddit.new(limit=POST_FETCH_LIMIT):

				# Ignore mod posts
				if submission.distinguished:
					continue

				# Ignore fully processed posts
				if submission.id in post_history:
					continue

				# Ignore manually approved posts
				if submission.approved:
					continue

				# Ignore old posts
				if get_age(submission.created_utc) > REINSTATE_PERIOD_MINUTES:
					continue

				# Ignored replied to posts
				if replied(submission):
					continue

				# Ignore update posts
				if "update" in submission.title.lower():
					continue

				# Check for post in database
				post_data = get_post(submission)
				# Process brand new post 
				if not post_data:
					# Remove user's outstanding posts from DB
					db_clear_user_posts(submission.author.name)
					logging.info("Adding to DB %s (%s)" % (submission.author.name, submission.shortlink))
					# Add new post to DB
					add_post(submission)
					logging.info("Prompting %s (%s)" % (submission.author.name, submission.shortlink))
					# Send user question prompt
					prompt_user(submission)
				post_history.append(submission.id)
				user_history.append(submission.author)


			# Check existing posts for removals or reinstatements
			process_post_queue()
			# Check messages for user answers
			process_inbox()

			# Prune post queues
			if len(post_history) > 1000:
				post_history.popleft()
				user_ihstory.popleft()

	except prawcore.exceptions.ServerError as e:
		logging.error("PRAW Error: %s" % str(e))
		time.sleep(10)
