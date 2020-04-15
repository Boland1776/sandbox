# To execute as a standalone script remove this line so the follwoing one is at the top
#!/usr/bin/env python

# This script MUST be called with python 2.x

import json
import re
import os
import datetime
import argparse

# For my test; script needs to be in /home/jenkins/hydrogen/workspace/Release_Engineering/Miscellaneous-Tools/cboland-sandbox/test

BASE_PATH    = 'http://artifactory.bullhorn.com:8081/artifactory/api/storage/bh-snapshots/com/bullhorn/'
DIR          = 'bullhorn-activity-center-0.1'
WORKING_FILE = "working.txt"
TESTING_FILE = "text.txt"
DELETE_FILE  = "delete_list.txt"
MAX_DAYS     = 20
USE_PARENT_DATE = False			# If True; check parent folder 'lastModified' time and remove all files under it
                                        # If False; ignore parent date and check all files in folder
USE_FILE_DATE   = not USE_PARENT_DATE	# Use one or the other date checks

# List of entries we want to skipp over (can be a regex pattern)
SKIP_LIST = ['/[0,1].[0,1]-SNAPSHOT',
             '/[0,1].[0,1].[0,1]-SNAPSHOT',
             '/master-SNAPSHOT',
             '/development-SNAPSHOT',
             '/support_20180[2,3,4,7,9]_fixes-SNAPSHOT'
            ]

tmp             = datetime.datetime.today()
todays_date_str = tmp.strftime("%Y-%m-%d")
todays_date     = tmp.strptime(todays_date_str, "%Y-%m-%d")
keep            = dict()
delete_list     = list()

parser = argparse.ArgumentParser(description='NPM artifact cleaner')
parser.add_argument('directory')
args = parser.parse_args()
DIR  = args.directory

print 'Grabbing %s data..' % DIR
base_path = BASE_PATH + DIR

# Pull the data from the specified direcotry
curl_str = 'curl ' + BASE_PATH + DIR + " -o " + TESTING_FILE + " > /dev/null 2>&1"
os.system(curl_str)
try:
  with open(TESTING_FILE) as fi:
    data = json.load(fi)
except IOError:
  print("Could not read \"%s\"" % TESTING_FILE)
  sys.exit(1)

for p in data['children']:					# Loop through folders (children key)
  if re.findall(r"(?=("+'|'.join(SKIP_LIST)+r"))", p['uri']):	# Skip any entries from SKIP_LIST
    print "  *skip: %s" % p['uri']
    continue

  # Save these entries in case we need to refer to them later
  keep['folder'] = p['folder']  # Should either be true or false
  keep['uri'] = p['uri']	# Folder or file name

# Pull the data from the child folder
  curl_str = 'curl ' + BASE_PATH + DIR + p['uri'] + " -o " + WORKING_FILE + " > /dev/null 2>&1"
  print '  process: %s' % p['uri']
  os.system(curl_str)

  print '    reading %s' % WORKING_FILE
  try:
    with open(WORKING_FILE) as fi:
      sub_data = json.load(fi)
  except IOError:
    print("Could not read \"%s\"" % WORKING_FILE)
    sys.exit(1)

  folder = sub_data['uri'].split('/')[-1]	# Grab the folder name from the whole path

  USE_FILE_DATE = not USE_PARENT_DATE	# USE_FILE_DATE may have changed below. Make sure it is set back to default here
  force_parent  = False			# Reset this flag to false
  if not 'children' in sub_data:	# If entry has no children (folders) force us to check parent date
    if USE_FILE_DATE:                   # Only do the force if we're checking files instead of parent
      print '  %s has no children .. forcing check of parent' % p['uri']
      force_parent = True		# Force a parent level check
      USE_FILE_DATE = not force_parent	# and turn off file level check

  if USE_PARENT_DATE or force_parent:  # No folder (children) to process so me MUST use parent
    print "    last modified date: %s" % sub_data['lastModified']
    tmp           = re.search(r'(.*)(-\d{2,}:\d{2,})', sub_data['lastModified'])# Strip off timezone
    tmp_time      = tmp.groups()[0]						# Save string w/o TZ
    file_dt       = datetime.datetime.strptime(tmp_time, '%Y-%m-%dT%H:%M:%S.%f')# Convert to datetime object
    file_date_str = datetime.datetime.strftime(file_dt, '%Y-%m-%d')		# Create a 'date' (only) string
    file_date     = datetime.datetime.strptime(file_date_str, '%Y-%m-%d')	# Create a 'date' (only) object

    # Now do simple date math. Subtract todays date from file date
    tdelta        = todays_date - file_date

    if tdelta.days > MAX_DAYS:
      del_str = base_path + p['uri']
      print '      delete: %s .. is > %d days old' % (folder, MAX_DAYS)
      print '        %s' % (del_str)
      delete_list.append(del_str)
#      delete_list.append(base_path + p['uri'] + folder)
    else:
      print '      keep: %s .. is <= %d days old' % (folder, MAX_DAYS)

  if USE_FILE_DATE:	# In case we want to process every file and not the parent
    for v in sub_data['children']:
      file_date = ''
      file_name = v['uri']
      match = re.search(r'(.*)(20\d{5,})(.*)', file_name)			# Find date in file name
      if match:
        file_date_str = match.groups()[1]					# Save that date as a string
        file_date     = datetime.datetime.strptime(file_date_str, '%Y%m%d')	# Convert string into object
        file_date_str = datetime.datetime.strftime(file_date, '%Y-%m-%d')	# Create date from object
        tdelta        = todays_date - file_date
        if tdelta.days > MAX_DAYS:
          del_str = base_path + p['uri'] + file_name
          print '      delete: %s .. is > %d days old' % (file_name, MAX_DAYS)
          print '        %s' % (del_str)
          delete_list.append(del_str)
#          delete_list.append(base_path + p['uri'] + file_name)
        else:
          print '      keep: %s .. is <= %d days old' % (file_name, MAX_DAYS)
      else:
        print "      no date on %s .. skipping" % file_name
        continue

# write these entries to a file and have pipeline read it to do actual delete
print 'Saving %d entries to %s' % (len(delete_list), DELETE_FILE)
with open(DELETE_FILE, 'w') as fi:
  for d in delete_list:
    fi.write('%s\n' % d)

print 'Cleaning up tmp files'
print 'rm ' + WORKING_FILE
print 'rm ' + TESTING_FILE
#os.system('rm ' + WORKING_FILE)
#os.system('rm ' + TESTING_FILE)

