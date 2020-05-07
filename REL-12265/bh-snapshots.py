# This script MUST be called with python 2.x
#!/usr/bin/env python
#
# Version 1.0.2 (05/07/2020)
#
# Called by Jenkins pipeline
# http://hydrogen.bh-bos2.bullhorn.com/Release_Engineering/Miscellaneous-Tools/cboland-sandbox/Working_Pipelines/<NA>
#
# See; REL-12265
#   http://artifactory.bullhorn.com/webapp/#/artifacts/browse/tree/General/bh-snapshots
#   Delete SNAPSHOT folder if it (or underlying SNAPSHOT artifacts) have not been modified in last 30 days
#   Do not delete folders with a name of
#     master-SNAPSHOT
#     development-SNAPSHOT
#     develop-SNAPSHOT
#     dev-SNAPSHOT

# Local storage: devartifactory1 : /art-backups/current/repositories/bh-snapshots

import json
import re
import os
import datetime
import argparse
import requests
import sys
import time
import subprocess
import shlex

FILES_COLLECTED = 0
# Max number of files to collect. There are over 725000 files so give the option to limit that
# setting to zero means no limit
MAX_FILES_TO_COLLECT = 0
FROM_OS = False   # Time stamps are OS , not Artifactory time stamp
MAX_DATA_SHOWN = False # Flag to only show when we reach max limt once

# When set, we collect all the data via curl and save that data for future test runs. If the flag is False, we dont
# run the "curl" and rely on the data saved (initially, these curls are taking 7+ hours (if run locally, days otherwise)
# with over 725,000 files).
GEN_SAVED_DATA = True   # By default we poll and save the data (False when debugging and we use saved catalog files)

HEADER1 = "=" * 80
HEADER2 = "#" * 80

# User options (some are only available via CLI)
DELETE_ONE = False  # Delete on file and exit : CLI and Jenkins (for now)
INTERACTIVE = False # Have user confirm deletion for each file (for debugging) : CLI
CLEAN     = True    # Clean any files I create (except the log) : CLI and Jenkins
LOG_DATA  = True
VERBOSE   = False   # Show what's being done : CLI and Jenkins
WAIT      = False   # Wait for user if true. : CLI
DO_DELETE = False   # Saftey measure. You MUST call script with "-D" to actually delete : CLI and Jenkins
MAX_DAYS  = 60      # Delete files older than this many days : CLI and Jenkins
USE_MODIFIED_TIME = True
USE_CREATED_TIME = False

# This is what we process
BASE_PATH      = 'http://artifactory.bullhorn.com:8081/artifactory/api/storage'
SNAPSHOT_PATH  = BASE_PATH + '/bh-snapshots'

# Misc files generated
LOG_FILE          = 'log.txt'            # Script output log
SNAPSHOT_CATALOG  = 'snap_catalog.txt'    # Where I store npm-dev results
KEEP_FILES        = 'keepers.txt'        # File NOT found in release repo but to young to delete.
DELETE_FILES      = 'deleters.txt'       # Where I store files to delete
SKIPPED_FILES     = 'skipped.txt'       # Where I store files/folders to skip (matched SKIP_FOLDERS)

# Skip the following FOLDERS in the npm-dev repo
# This list can be appended to via the CLI "-S <list,of,folders>" option (comma seperated) or, from Jenkins
# by adding folders in "SKIP_LIST"
SKIP_FOLDERS = ['master-SNAPSHOT', 'development-SNAPSHOT', 'develop-SNAPSHOT', 'dev-SNAPSHOT']

# Skip FILES in this list. For now I skip variants of "DO_NOT_DELETE" in the file name, and the file package.json
# This list can only be modified here (for now).
SKIP_FILES = ['DONOTDELETE', 'DO_NOT_DELETE', 'DONTDELETE', 'DONT_DELETE', 'maven-metadata.xml']

tmp             = datetime.datetime.today()
todays_date_str = tmp.strftime("%Y-%m-%d")
timestamp       = tmp.strftime("%Y%m%d%H%M")
todays_date     = tmp.strptime(todays_date_str, "%Y-%m-%d")

skipped = list()

def collect_data(uri):
    """ Collect URI data via curl and return output in a dict.  """
    global FILES_COLLECTED

    data = list()

    # Some file names have spaces and/or parenthesis and don't seem to play well with curl. I have tried escaping the
    # string with no luck. For now, a simpler solution is to wrap the uri in quotes. So I do that here
    curl_str = 'curl "' + uri + '"'
#    short = '/'.join(uri.split('/')[-3:])
    short = uri
    short = short.replace(SNAPSHOT_PATH, '')
    lprint ('%5d) Processing: %s' % (FILES_COLLECTED + 1, short), False)
#    lprint ('%5d) Processing: %s' % (FILES_COLLECTED, uri), False)

    args = shlex.split(curl_str)                                    # Convert cmd to shell-like syntax
    with open(os.devnull, 'w') as DEV_NULL:                         # Open file descriptor to /dev/null
        try:                                                        # Try and run the curl command
            out = subprocess.check_output(args, stderr=DEV_NULL)    # If success, "out" has our data
        except subprocess.CalledProcessError as e:                  # If subPorcess error, report that here
            lprint('! subprocess ERROR %s' % e.output, False)
        except:                                                     # All other exceptions here
            lprint('! Unknown ERROR: Sys: %s' % sys.exc_info()[0], False) # And try to show what caused the issue
        else:                                                           # No exception, so continue processing..
            try:                                                        # Try and convert <out> data to JSON
                data = json.loads(out)                                  # Ok, we converted to JSON
                if 'errors' in data:                                    # Sometimes the curl worked but we get bad data
                    lprint('! ERROR: Curl request returned: %s' % data, False)  # Show the error returned by curl
                    data = list()                                       # Return empty dict
            except ValueError as e:                                     # Some pesky files don't produce any output :(
                lprint('! ValueError: Could not convert data to JSON', False) # So log it and move on
            except:                                                     # Grab all other exceptions here
                lprint('! Unknown ERROR: Sys: %s' % sys.exc_info()[0], False)  # Get error from system call

    return data         # Return the data dict (whether it has data or is None)

def traverse(data, catalog):
    """ Recursively traverse through folders looking for files. """

    global skipped, FILES_COLLECTED, MAX_FILES_TO_COLLECT, MAX_DATA_SHOWN

    # If the data has a 'children' key then I need to process it further
    for c in data['children']:
        if MAX_FILES_TO_COLLECT > 0 and FILES_COLLECTED >= MAX_FILES_TO_COLLECT:
            if MAX_DATA_SHOWN == False:  # Inly show this message once
                lprint('Collected %d files .. break' % FILES_COLLECTED, True)
                MAX_DATA_SHOWN = True
            return(catalog)
        FILES_COLLECTED = FILES_COLLECTED + 1
#        lprint ('File: %d' % FILES_COLLECTED, False)

        # If processing the 'DEV' repo, check to see if this is a folder in the SKIP_FOLDERS
        if len(SKIP_FOLDERS) > 0:      # Make sure there is something to skip

            # See if this folder (data['path']) is in our list of folders to skip (SKIP_FOLDERS)
            # If so, skip it by returning
            if re.findall(r"(?=("+'|'.join(SKIP_FOLDERS)+r"))", data['path']):
                lprint ('! skipping folder: %s' % data['uri'], False)
                skipped.append('Skip Folder: ' + data['uri'])
                return(catalog)     # There was a match so return w/o further processing

        # If here, this is a valid file/folder to process
        # Create the full path (new_path) with child name. If it has a 'folder' key we traverse deeper. If not, this
        # must be a file and I obtain the creation, or lastModified, date from the <new_path>
        new_path = SNAPSHOT_PATH + data['path'] + c['uri']
        new_data = collect_data(new_path)               # Get (curl) data on new path

        # If nothing is returned that's an issue. Add it to the 'skip' list and log it as needing "Attention"
        if new_data == None:
            lprint ('! skipping null dict: %s' % c['uri'], False)
            skipped.append('Attention: ' + data['uri'] + c['uri'])
        else:
            if c['folder']:                  # If the child is a folder
                traverse(new_data, catalog)  # traverse to the next level
            else:                            # If not a folder, new_data contains the date info we need

                if len(SKIP_FILES) > 0:      # Make sure there is something to skip
            # If a file name contains "DO_NOT_DELETE" (or some variant thereof), or 'package.json', skip it
                    if re.findall(r"(?=("+'|'.join(SKIP_FILES)+r"))", c['uri']):
                        lprint ('! skipping file: %s' % c['uri'], False)        # Show file (only) for readability
                        skipped.append('Skip File: ' + data['uri'] + c['uri'])  # Save full path of file to skip
                        return(catalog)

                file = data['uri'] + c['uri'] # File is full path

                # In some cases curl is returning no date info and I haven't been able to figure out why. It seems
                # to occur on files with spaces and/or parenthesis in the file name. In any case, until I figure it
                # out, I will mark the file as 'skipped'.
                if len(new_data) == 0:
                    lprint ('! ignore: no date found (%s)' % file, False)
                    lprint ('! skipping null date: %s' % c['uri'], False)   # Show file (only) for readability
                    skipped.append('Null date: ' + data['uri'] + c['uri'])
                else:
            # I think we should be using the lastModified date instead of created date. Some files have a
            # created date of years ago (package.json) while its lastModified date is within a day of current date
                    if USE_MODIFIED_TIME:
                        catalog[file] = new_data['lastModified'] # Save modified date in dict with <file> as key
                    else:
                        catalog[file] = new_data['created']      # Save created date in dict with <file> as key

    return(catalog)

def read_data(file):
    """ Read the saved outut of a real run. Each line is a K|V pair to repopulate the dicts """
    data = list()
    dct  = dict()

    lprint ('Reading "%s"' % file, False)
    if not os.path.exists(file):
        lprint ('"%s" not found! (try running w/o "-g" option) .. exiting' % file, False)
        sys.exit(1)

    # Read a saved file and store in a list
    with open(file) as fi:
        for line in fi:
            data.append(line)

    # Process the list into dictionary key value pairs and populate a new dict (to return)
    for x in data:
        x = x.strip()

        # There should only be two values (key|value). If there are more or less that's an issue, so skip it
        if len(x.split('|')) != 2:
            lprint ('  "%s" does not have two fields (%d) .. skipping' % (x, len(x.split('|'))), False)
            continue

        (k, v) = x.split("|")
        dct[k] = v

    return(dct) # Return the new dictionary

# For debugging, no need to log this output via lprint
def show_catalog(cat):
    """ Show the catalog's dictionary """

    print '\nDisplay catalog, %d entries..' % len(cat)
    raw_input('Press Enter when ready to view')
    for k in sorted(cat):
        print '%9s :: %s' % (cat[k], k)

def save_catalog(dct, file):
    """ Save the catalog dictionary (dct) to file (file) """

    lprint ('Saving catalog "%s"' % file, False)
    with open(file, 'w') as fo:
        for k in sorted(dct):
            fo.write('%s|%s\n' % (k, dct[k]))

def write_list(file, lst):
    """ Write a list (lst) to file (file) """

    lprint ('Writing list to %s' % file, False)
    with open(file, 'w') as file_ptr:
        # Since we sort the list when we write I can't merely insert these comments into the list. I must write them to
        # the file then write the sorted data behind it.
        if file == SKIPPED_FILES:
            file_ptr.write("%s\n" % HEADER2)
            file_ptr.write("# These files were skipped (will not be removed) due to one or more of the following ..\n")
            file_ptr.write("# 1) There was an issue processing the file date\n")
            file_ptr.write("# 2) The file matches one of these names..\n")
            tmp = '#      {}'.format(', '.join(SKIP_FILES))
            file_ptr.write('%s\n' % tmp)
            file_ptr.write("#\n# 3) The folder matches one of these names..\n")
            tmp = '#      {}'.format(', '.join(SKIP_FOLDERS))
            file_ptr.write('%s\n' % tmp)
            file_ptr.write("#\n%s\n" % HEADER2)
        elif file == KEEP_FILES:
            file_ptr.write("%s\n" % HEADER2)
            file_ptr.write("# These files were not found in the release repo but are < %d days old (will be kept)\n" % MAX_DAYS)
            file_ptr.write("%s\n" % HEADER2)
        elif file == DELETE_FILES:
            file_ptr.write("%s\n" % HEADER2)
            file_ptr.write("# These files are marked for deletion because they are not in the release repo, are not in one of the skip lists\n")
            file_ptr.write("# and their lastModified date is > %d days\n" % MAX_DAYS)
            file_ptr.write("%s\n" % HEADER2)

        for k in sorted(lst):
            file_ptr.write('%s\n' % k)

def delete_files(lst, u, p):
    """ Delete the files obtained from the delete list.
        See: https://en.wikipedia.org/wiki/List_of_HTTP_status_codes   for return codes
    """
    user_skip = False

    lprint('%d files to delete ..' % len(lst), False)
    for file in lst:
        if file.startswith('#'):    # Skip any comment in the file
            continue

        if DO_DELETE:   # Option to delete the files from artifactory is set!

            # To delete the file we must reformat the path aquired and remove the string '/api/storage'
            # from the path. If we do not do this calls to delete will return "400" (bad request).
            file = file.replace('/api/storage', '')
#            lprint('  "%s"' % file, False) # Show the file to be deleted

            if INTERACTIVE:         # Ask the user to confirm the deletion of each file
                user_skip = False   # Set to initial state
                lprint ('%s' % file, False)
                ans = raw_input('Ok to delete [y/n/q]: ')
                if 'y' in ans:
                    lprint ('deleteing "%s"' % file, False)
                    resp = requests.delete(file, auth=(u, p))
                elif 'q' in ans:
                    return
                else:
                    lprint ('skipping "%s"' % file, False)
                    user_skip = True
            elif DELETE_ONE:   # Delete the first file listed and return (for dbg), if delete fails try the next one
                lprint ('deleteing "%s"' % file, False)
                resp = requests.delete(file, auth=(u, p))
                if 200 <= resp.status_code <= 299:  # Success values (200-299)
                    lprint ('Success: request status returned: %d' % resp.status_code, True)
                    return
            else:           # Not interacive, just delete files as they come
                lprint ('deleteing "%s"' % file, False)
                resp = requests.delete(file, auth=(u, p))
        else:           # Delete option is noit set. Run a simple "get" so we can test a transaction
            lprint ('"get" "%s"' % file, False)
            resp = requests.get(file, auth=(u, p))

        # This is just what to display on the 'request' action we took.
        # If there was no 'requuest' action on the file we show some info on the 'request' return code
        # else, don't show data on a file where no action was taken
        if user_skip == False:
            if not 200 <= resp.status_code <= 299:  # Success values (200-299)
                lprint ('* Warning: %s' % resp, False)
                lprint ('  a non-success value was returned!', True)
            else:
                lprint ('Success: request status returned: %d' % resp.status_code, False)

def parse_options():
    """ Parse options that are set in the environment (from Jenkins) """

    global VERBOSE, SKIP_FOLDERS, MAX_DAYS, CLEAN, DO_DELETE, DELETE_ONE, MAX_FILES_TO_COLLECT

    tmp = os.getenv("VERBOSE")
    if tmp and tmp.lower() in ['true', '1']:
        VERBOSE = True

    tmp = os.getenv("DELETE_ONE")               # Used as a test. Run scans but exit after deleting one file
    if tmp and tmp.lower() in ['true', '1']:
        DELETE_ONE = True

    tmp = os.getenv("DO_DELETE")               # Used as a test. Run scans but exit after deleting one file
    if tmp and tmp.lower() in ['true', '1']:
        DO_DELETE = True

    tmp = os.getenv("KEEP_FILES")
    if tmp and tmp.lower() in ['true', '1']:
        CLEAN = False

    tmp = os.getenv("USE_CREATED_TIME")
    if tmp and tmp.lower() in ['true', '1']:
        USE_CREATED_TIME = True
        USE_MODIFIED_DATE = False

    tmp = os.getenv("SKIP_FOLDERS")
    if tmp:
        SKIP_FOLDERS = tmp.split(',')

    tmp = os.getenv("MAX_DAYS")
    if tmp:
        MAX_DAYS = int(tmp)

    tmp = os.getenv("MAX_FILES_TO_COLLECT")
    if tmp:
        MAX_FILES_TO_COLLECT = int(tmp)

def cleanup_temp_files():
    """ Clean up temp files """

    files = [KEEP_FILES, DELETE_FILES, SKIPPED_FILES, SNAPSHOT_CATALOG]

    for f in files:
        if os.path.exists(f):
            lprint ('rm ' + f, False)
            try:
                os.system('rm ' + f)
            except:
                lprint ('  could not remove %s' % f, False)

def lprint(msg, wait):
    """ Log and print a message """
    global timestamp

    if LOG_DATA:
        with open(LOG_FILE, 'a') as lf:
            lf.write(msg + '\n')

    if VERBOSE:                     # Verbose is set
        if wait:                    # And we requested user input
            if WAIT:                # And the WAIT option was issued
                raw_input(msg)      # So, wait for user
            else:                   # WAIT not issued
                print msg           # Show message
                sys.stdout.flush()  # Make sure we flush the msg before sleeping
                time.sleep(2)       # And delay (instead of wait)
        else:
            print msg               # Else, just print message
    else:
        if re.match(r'\* Warning', msg, re.IGNORECASE):
            print msg
    sys.stdout.flush()              # One final flush for the rest


def main():
    """ Main loop of script """

    global MAX_DAYS, VERBOSE, INTERACTIVE, GEN_SAVED_DATA, SKIP_FOLDERS, WAIT, DO_DELETE, CLEAN, MAX_FILES_TO_COLLECT
    global skipped

    snap_catalog = dict()
    keep         = list()
    delete       = list()
    skipped      = []

    # These are only used for running on CLI. Jenkins passes its params (except creds) via env-vars in OS
    parser = argparse.ArgumentParser(description='NPM artifact cleaner')
    parser.add_argument('-d', '--days', help='Remove files older than this value', type=int)
    parser.add_argument('-m', '--max_files', help='Maximum number of files to collect', type=int)
    parser.add_argument('-c', '--create_time', help='Use file created time (instead of lastModified)', action='store_true')
    parser.add_argument('-k', '--keep_file', help='Dont keep_file temp files', action='store_true')
    parser.add_argument('-o', '--delete_one', help='Delete one file and exit', action='store_true')
    parser.add_argument('-i', '--interactive', help='User confrims each delete', action='store_true')
    parser.add_argument('-v', '--verbose', help='Be verbose in processing', action='store_true')
    parser.add_argument('-g', '--generate', help='Dont generate saved data files', action='store_true')
    parser.add_argument('-w', '--wait', help='Wait for user (should only be used on CLI)', action='store_true')
    parser.add_argument('-D', '--delete', help='Set this flag to actually delete the files', action='store_true')
    parser.add_argument('-S', '--skip', help='Comma seperated list of folders to add to internal SKIP_FOLDERS', type=str)
    parser.add_argument('-u', '--user', help='username', required=True, type=str)
    parser.add_argument('-p', '--password', help='passwd', required=True, type=str)

    args   = parser.parse_args()
    user   = args.user
    passwd = args.password

    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)

    if args.days:
        MAX_DAYS = args.days

    if args.max_files:
        MAX_FILES_TO_COLLECT = args.max_files

    # If we have CLI options we must set an env-var to be consistent.
    # options not set as env-var are options Jenkins doesn't have
    if args.interactive:
        INTERACTIVE = True  # Confirm each delete
        VERBOSE = True      # We have to see what we're doing
        WAIT = True         # Wait for user input
        CLEAN = False       # Dont delete files
        os.environ["DO_DELETE"]  = "1"
    if args.delete_one:
        os.environ["DELETE_ONE"] = "1"
        os.environ["DO_DELETE"]  = "1"
    if args.delete:
        os.environ["DO_DELETE"] = "1"
    if args.verbose:
        os.environ["VERBOSE"] = "1"
    if args.keep_file:
        os.environ["KEEP_FILES"] = "1"
    if args.create_time:
        os.environ["USE_CREATED_TIME"] = "1"
    if args.wait:
        WAIT = True
    if args.generate:
        GEN_SAVED_DATA = False  # Rely only on saved file data (for debugging)
    if args.skip:               # Add these items to our skip list
        new_list = args.skip.split(',')
        SKIP_FOLDERS = SKIP_FOLDERS + new_list
        os.environ["SKIP_FOLDERS"] = ','.join(SKIP_FOLDERS)
        if not GEN_SAVED_DATA:
            print 'Warning: "-S" should not be used with "-g" (since we will be generating new data)'

    parse_options()     # Parse any env-var options Jenkins sent

    if DO_DELETE:
        lprint ('** Delete option is set **', True)
        if DELETE_ONE:
            lprint ('** Delete one file and exit is also set **', False)

    # Log, and maybe show, which options were called
    lprint ('\nScript called with these options..', False)
    lprint ('WAIT: %s' % WAIT, False)
    lprint ('VERBOSE: %s' % VERBOSE, False)
    lprint ('MAX_DAYS: %d' % MAX_DAYS, False)
    lprint ('MAX_FILES: %d' % MAX_FILES_TO_COLLECT, False)
    lprint ('DO_DELETE: %s' % DO_DELETE, False)
    lprint ('DELETE_ONE: %s' % DELETE_ONE, False)
    lprint ('INTERACTIVE: %s' % INTERACTIVE, False)
    lprint ('CLEAN UP FILES: %s' % CLEAN, False)
    lprint ('USE CREATED TIME: %s' % USE_CREATED_TIME, False)
    lprint ('USE MODIFIED TIME: %s' % USE_MODIFIED_TIME, False)
    lprint ('SKIP FILES: %s' % ', '.join(SKIP_FILES), False)
    lprint ('SKIP FOLDERS: %s' % ', '.join(SKIP_FOLDERS), False)

    # I could process the data w/o saving it but the data is useful for debugging and running multiple time
    # without having to constantly send requests to artifactory
    if GEN_SAVED_DATA:  # Scan the artifactory folders and save the data
        lprint ('\nGenerating bh-snapshots catalog: %s\n%s' % (SNAPSHOT_PATH, HEADER1), False)
        snapshot_base = collect_data(SNAPSHOT_PATH)
        traverse(snapshot_base, snap_catalog)
        save_catalog(snap_catalog, SNAPSHOT_CATALOG)

    else:               # Don't scan artifactory, use data from previous run
        lprint ('Using saved data', False)
        snap_catalog = read_data(SNAPSHOT_CATALOG)
        lprint ('%d files read from %s' % (len(snap_catalog), SNAPSHOT_CATALOG), False)

    # Now that I Have a list of files, with selected files/folders omitted, I can see if the file lastMod date
    # is > MAX_DAYS
    for dev_file in sorted(snap_catalog):    # Loop through the development files
        lprint ('Processing: %s' % dev_file, False)
        file_name = dev_file.split('/')[-1] # Just the file with no path

#        print 'DATE: %s' % snap_catalog[dev_file]
        if FROM_OS:   # Date is formatted differently
            tmp           = snap_catalog[dev_file]    # Strip off timezone
            file_dt       = datetime.datetime.strptime(tmp, '%a %b %d %H:%M:%S %Y') # Convert dev_file to datetime obj
#            file_dt       = datetime.datetime.strptime(tmp, '%Y-%m-%dT%H:%M:%S.%f') # Convert dev_file to datetime obj
            file_date_str = datetime.datetime.strftime(file_dt, '%Y-%m-%d')              # Create a 'date' (only) string
            file_date     = datetime.datetime.strptime(file_date_str, '%Y-%m-%d')        # Create a 'date' (only) object
#            lprint('TMP: %s' % tmp, False)
#            lprint('FILE_DATE_STR: %s' % file_date_str, False)
#            lprint('FILE_DATE: %s' % file_date, False)
        else:
            tmp           = re.search(r'(.*)(-\d{2,}:\d{2,})', snap_catalog[dev_file])   # Strip off timezone
            tmp_time      = tmp.groups()[0]                                              # Save string w/o TZ
            file_dt       = datetime.datetime.strptime(tmp_time, '%Y-%m-%dT%H:%M:%S.%f') # Convert dev_file to datetime obj
            file_date_str = datetime.datetime.strftime(file_dt, '%Y-%m-%d')              # Create a 'date' (only) string
            file_date     = datetime.datetime.strptime(file_date_str, '%Y-%m-%d')        # Create a 'date' (only) object
#            lprint('FILE_DT: %s' % file_dt, False)
#            lprint('FILE_DATE_STR: %s' % file_date_str, False)
#            lprint('FILE_DATE: %s' % file_date, False)
        delta         = todays_date - file_date

        if delta.days > MAX_DAYS:
            lprint ('  -> file is not in releases, is %d days old (%d is cutoff) .. marked for removal' % (delta.days, MAX_DAYS), False)
            delete.append(dev_file)         # Put this file in the delete list
        else:
            lprint ('  -> file is not in releases, but only %d days old (%d is cutoff) .. file kept' % (delta.days, MAX_DAYS), False)
            keep.append(dev_file)           # Files NOT in release repo but too yuong
#        lprint('Next', True)

    lprint ('', False)
    lprint('%4d entries in bh-snapshot repo' % len(snap_catalog), False)
    lprint('%4d entries skipped (from SKIP_FOLDERS)' % len(skipped), False)
    lprint('%4d entries kept (Too young)' % len(keep), False)
    lprint('%4d entries to delete' % len(delete), False)
    lprint ('', False)
    write_list(KEEP_FILES, keep)
    write_list(DELETE_FILES, delete)
    write_list(SKIPPED_FILES, skipped)
    lprint ('', False)
    if not DO_DELETE:
        lprint ('File deletion skipped', False)
    else:
        delete_files(delete, user, passwd)

    lprint ('', False)

    if CLEAN:      # Clean temp files unless user said 'no'
        cleanup_temp_files()
    else:
        lprint ('\nKeeping temporary files', False)

    lprint ('\nJob complete\n', False)

if __name__ == '__main__':
    main()
