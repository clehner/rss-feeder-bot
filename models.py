from google.appengine.ext import db

class Feed(db.Model):
  url = db.StringProperty()
  title = db.StringProperty()

class Entry(db.Model):
  feed = db.ReferenceProperty(Feed)
  id = db.StringProperty()
  
class Subscription(db.Model):
  wave_id = db.StringProperty()
  wave_json = db.TextProperty()
  feed = db.ReferenceProperty(Feed)