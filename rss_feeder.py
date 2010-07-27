import logging
from django.utils import simplejson
import feedparser
import re

from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.api import urlfetch
from google.appengine.api.labs import taskqueue

from waveapi import appengine_robot_runner
from waveapi import element
from waveapi import events
from waveapi import robot

import credentials
import models
from append_content import append_content_to_blip

rss_bot = None

def on_wavelet_self_added(event, wavelet):
    # If the root blip is empty, use it. Otherwise make a new blip.
    if wavelet.root_blip.text == "\n":
        blip = wavelet.root_blip
    else:
        blip = wavelet.reply()
    add_subscription_form_to_blip(blip)

def on_wavelet_self_removed(event, wavelet):
    logging.info('we were removed from a wave')
    unsubscribe_wave(wavelet)

def add_subscription_form_to_blip(blip):
    blip.append('RSS Feed Reader\n')
    blip.append(element.Label('feed_url_input_label', 'Feed URL:'))
    blip.append(element.Input('feed_url_input_element'))
    blip.append(element.Button('subscribe_button_element', 'Subscribe'))
    blip.append(element.Button('unsubscribe_button_element', 'Unsubscribe'))

def on_form_button_clicked(event, wavelet):
    '''
    Called when a form button is clicked
    '''
    if event.button_name == 'subscribe_button_element':
        # Subscribe button was clicked.
        
        feed_url_input = event.blip.first(element.Input,
                                          name='feed_url_input_element')
        feed_url = feed_url_input.value().value.strip()
        if feed_url:
            at_root = (event.blip == wavelet.root_blip)
            subscribe_wave_to_feed_url(wavelet, feed_url, at_root=at_root)
        
    elif event.button_name == 'unsubscribe_button_element':
        # Unsubscribe button was clicked.
        
        unsubscribe_wave(wavelet)

def unsubscribe_wave(wavelet):
    logging.info('unsubscribed wave')
    subscription = get_wave_subscription(wavelet)
    if subscription is None:
        return
    feed = subscription.feed
    subscription.delete()
    check_remaining_subscriptions_for_feed(feed)

def check_remaining_subscriptions_for_feed(feed):
    # If there are no more subscriptions to this feed,
    # delete the feed so we stop checking it.
    q = models.Subscription.all()
    q.filter('feed = ', feed)
    if q.count(1) == 0:
        delete_feed(feed)


def get_wave_subscription(wavelet):
    q = models.Subscription.all()
    q.filter('wave_id = ', wavelet.wave_id)
    subscription = q.get()
    return subscription

def subscribe_wave_to_feed_url(wavelet, feed_url, at_root=False):
    subscription = get_wave_subscription(wavelet)
    if subscription:
        # This wave is already subscribed to a feed.
        logging.info('already subscribed')
        if subscription.feed.url == feed_url:
            # It's the same feed.
            logging.info('same feed')
            update_feed(subscription.feed)
            return
        else:
            logging.info('changing subscription')
            check_remaining_subscriptions_for_feed(subscription.feed)
    else:
        logging.info('new subscription')
        # New subscription
        subscription = models.Subscription()
        subscription.wave_id = wavelet.wave_id
    # Update subscription
    subscription.wave_json = simplejson.dumps(wavelet.serialize())
        
    update_now = False
    
    feed = models.Feed.all().filter('url = ', feed_url).get()
    if feed:
        # We already have this feed in the db.
        # Add its existing entries to the wave.
        try:
            feed_contents = urlfetch.fetch(feed_url).content
            d = feedparser.parse(feed_contents)
            append_entries_to_wave(wavelet, d['entries'])
        except:
            pass
    else:
        # It is a new feed.
        feed = models.Feed()
        feed.url = feed_url
        feed.put()
        update_now = True
    
    subscription.feed = feed
    subscription.put()
    
    if at_root and feed.title:
        wavelet.title = feed.title
    
    if update_now:
        update_feed(feed)

def queue_update_feed(feed_url):
    taskqueue.add(url='/update_feed', params={'feed_url': feed_url})

class FeedUpdaterTask(webapp.RequestHandler):
    def post(self):
        feed_url = self.request.get('feed_url')
        feed = models.Feed.all().filter('url = ', feed_url).get()
        if feed:
            update_feed(feed)

class FeedsUpdaterCron(webapp.RequestHandler):
    '''
    Update all feeds (using tasks). Called by cron.
    '''
    def get(self):
        logging.info('updating all feeds')
        feeds = models.Feed.all()
        for feed in feeds:
            queue_update_feed(feed.url)

def update_feed(feed):
    logging.info('updating a feed')

    try:
        feed_contents = urlfetch.fetch(feed.url).content
        d = feedparser.parse(feed_contents)
    except:
        return
    feed.title = d.feed.get('title')
    feed.put()
    new_entries = []
    # Check each entry of the feed to find the new ones.
    #logging.info('all entries: %s' % d['entries'])
    for entry in d.get('entries', []):
        entry_id = entry.get('id') or entry.get('link')
        entry_query = models.Entry.all(keys_only=True)
        entry_query.filter('feed = ', feed)
        entry_query.filter('id = ', entry_id)
        entry_is_new = (entry_query.count(1) == 0)
        if entry_is_new:
            # Record the entry.
            models.Entry(feed=feed, id=entry_id).put()
            # Queue this entry to be sent to the waves.
            new_entries.append(entry)
    logging.info('new entries: %s' % new_entries)
    if new_entries:
        append_entries_to_waves_with_feed(new_entries, feed)

def append_entries_to_waves_with_feed(entries, feed):
    '''
    Append entries to all the waves with this feed.
    '''
    logging.info('appending entries')
    subscriptions = models.Subscription.all().filter('feed = ', feed)
    
    logging.info('got %s waves to update' % subscriptions.count())
    
    first_wave = None
    for subscription_result in subscriptions:
        subscription_wave = rss_bot.blind_wavelet(subscription_result.wave_json)
        append_entries_to_wave(subscription_wave, entries)
        if first_wave:
            subscription_wave.submit_with(first_wave)
        else:
            first_wave = subscription_wave
    # Submit all the waves together with the first one.
    logging.info('first_wave: %s' % first_wave)
    if first_wave:
        setup_oauth(first_wave.domain)
        #try:
        rss_bot.submit(first_wave)
        #except urlfetch.DownloadError:
            #rss_bot.submit(first_wave)
    else:
        # No waves subscribe to this feed anymore, so delete the feed
        delete_feed(feed)

def append_entries_to_wave(wavelet, entries):
    #logging.info('appending entries (%s) to wave (%s)' % (entries, wavelet))
    entries = sorted(entries, key=lambda entry: entry.updated_parsed)
    for entry in entries:
        new_blip = wavelet.reply()
        
        title = entry.get('title')
        link = entry.get('link')
        if link and not title:
            title = link
            link = None
        if title:
            new_blip.append(element.Line(line_type='h4'))
            if link:
                new_blip.append(title, [('link/manual', link)])
                new_blip.append('\n', [('link/manual', None)])
            else:
                new_blip.append(title + '\n')
        
        contents = entry.get('content') or [entry.get('summary_detail')]
        for content in contents:
            append_content_to_blip(new_blip, content.value, content.type)

def delete_feed(feed):
    '''
    Delete a feed model and its associated entry models.
    '''
    logging.info('deleting feed %s' % feed)
    entries = models.Entry.all(keys_only=True).filter('feed = ', feed)
    db.delete(entries)
    feed.delete()

def setup_oauth(domain):
    if domain not in credentials.RPC_BASE:
        domain = 'googlewave.com'
    rss_bot.setup_oauth(credentials.KEY, credentials.SECRET,
                        server_rpc_base=credentials.RPC_BASE[domain])


if __name__ == '__main__':
    rss_bot = robot.Robot('RSS Feeder Bot', 
            image_url='http://rss-feeder.appspot.com/static/feed-icon.png',
            profile_url='http://rss-feeder.appspot.com/')
    rss_bot.register_handler(events.WaveletSelfAdded, on_wavelet_self_added)
    rss_bot.register_handler(events.WaveletSelfRemoved, on_wavelet_self_removed)
    rss_bot.register_handler(events.FormButtonClicked, on_form_button_clicked)
    rss_bot.set_verification_token_info(credentials.VERIFICATION_TOKEN,
                                        credentials.ST)
    appengine_robot_runner.run(rss_bot, debug=True, extra_handlers=[
            ('/update_feed', FeedUpdaterTask),
            ('/update_all_feeds', FeedsUpdaterCron)
    ])