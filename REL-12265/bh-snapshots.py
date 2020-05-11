# This script MUST be called with python 2.x
#!/usr/bin/env python
#
# Version 1.0.5 (05/11/2020)
#
# Called by Jenkins pipeline
# http://hydrogen.bh-bos2.bullhorn.com/Release_Engineering/Miscellaneous-Tools/cboland-sandbox/Working_Pipelines/<NA>
#
# This extends/obsoletes
#   Jenkins Pileline: http://hydrogen.bh-bos2.bullhorn.com/job/Release_Engineering/job/Practice/job/Artifactory-Clean-Up/
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
# Max number of files to collect. As of May 2020 were over 725,000 files; give the option to limit that
# setting to zero means no limit
MAX_FILES_TO_COLLECT = 1000    # By default process 1000 files. Jenkins may set to something else

# There are so many files in this repo, and running this remotely can take days, I have a test script that collects
# all files directly but they return the OS timestamp when I was expecting an Artifactory timestamp
FROM_OS = False         # If True, process the OS timestamp. False, process Artifactory timestamp

# Processing data will likely be a few layers deep in the traverse function. If we hit the MAX_FILES_TO_COLLECT threshold
# we begin backing out of those recursive calls.
MAX_DATA_SHOWN = False  # This flag allows me to only show the "exitting.." message once

# When set, we collect all the data via curl and save that data for future test runs. If the flag is False, we dont
# run the "curl" and rely on the data saved (initially, these curls are taking 7+ hours (if run locally, days otherwise)
GEN_SAVED_DATA = True   # By default we poll and save the data (False when debugging and I use saved catalog files)

HEADER1 = "=" * 90  # Output file header
HEADER2 = "#" * 90  # Output file header

# User options (some are only available via CLI)
DELETE_ONE = False  # Delete one file and exit : CLI and Jenkins (for now)
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
LOG_FILE          = 'log.txt'           # Script output log
SNAPSHOT_CATALOG  = 'snap_catalog.txt'  # Where I store bh-snapshots results with timestamp
KEEP_FILES        = 'keepers.txt'       # File too young to delete.
DELETE_FILES      = 'deleters.txt'      # Files to delete
SKIPPED_FILES     = 'skipped.txt'       # Files/folders to skip (matched SKIP_FOLDERS/FILES)

# Skip the following FOLDERS in the SNAPSHOT_PATH repo
# This list can be appended to via the CLI "-S <list,of,folders>" option (comma seperated) or, from Jenkins
# by adding folders in "SKIP_LIST"
SKIP_FOLDERS = ['master-SNAPSHOT', 'development-SNAPSHOT', 'develop-SNAPSHOT', 'dev-SNAPSHOT']

# Skip FILES in this list. For now I skip variants of "DO_NOT_DELETE" in the file name, and the file "maven-metadata.xml"
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
    short = uri                                 # Create a short name for easier viewing
    short = short.replace(SNAPSHOT_PATH, '')    # by removing the SNAPSHOT_PATH from the full path in the uri
    lprint ('%5d) Processing: %s' % (FILES_COLLECTED + 1, short), False)

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
        if len(SKIP_FOLDERS) > 0:   # If our skip list has at least one entry...

            # See if this folder (data['path']) is in our list of folders to skip (SKIP_FOLDERS)
            # If so, skip it by returning
            if re.findall(r"(?=("+'|'.join(SKIP_FOLDERS)+r"))", data['path']):
                lprint ('! skipping folder: %s' % data['uri'], False)
                skipped.append('Skip Folder: ' + data['uri'])
                return(catalog)     # There was a match so return w/o further processing

        if MAX_FILES_TO_COLLECT > 0 and FILES_COLLECTED >= MAX_FILES_TO_COLLECT:
            if MAX_DATA_SHOWN == False: # Message has not been displayed yet.
                lprint('Collected %d files .. exitting' % FILES_COLLECTED, True)
                MAX_DATA_SHOWN = True   # Set to True to indicate I displayed the message
            return(catalog)
        FILES_COLLECTED = FILES_COLLECTED + 1

        # If here, this is a valid file/folder to process
        # Create the full path <new_path> with child name. If it has a 'folder' key we traverse deeper. If not, this
        # must be a file and I obtain the creation, or lastModified, date from the <new_path>
        new_path = SNAPSHOT_PATH + data['path'] + c['uri']  # Set new path to traverse
        new_data = collect_data(new_path)                   # Get (curl) data on new path

        # If nothing is returned that's an issue. Add it to the 'skip' list and log it as needing "Attention"
        if new_data == None:
            lprint ('! skipping null dict: %s' % c['uri'], False)
            skipped.append('Attention: ' + data['uri'] + c['uri'])

        else:                                # <new_data> has data, so process it.
            if c['folder']:                  # If the child is a folder
                traverse(new_data, catalog)  # traverse to the next level
            else:                            # If not a folder, <new_data> contains the date info we need

                if len(SKIP_FILES) > 0:      # If I have one or more files to skip check that now.
            # If a file name contains "DO_NOT_DELETE" (or some variant thereof), or 'maven-metadata.xml', skip it
                    if re.findall(r"(?=("+'|'.join(SKIP_FILES)+r"))", c['uri']):
                        lprint ('! skipping file: %s' % c['uri'], False)        # Show file (only) for readability
                        skipped.append('Skip File: ' + data['uri'] + c['uri'])  # Save full path of file to skip
                        return(catalog)

                file = data['uri'] + c['uri'] # Save full path in <file>

                # In some cases curl is returning no date info and I haven't been able to figure out why. It seems
                # to occur on files with spaces and/or parenthesis in the file name. In any case, until I figure it
                # out, I will mark the file as 'skipped'.
                if len(new_data) == 0:      # See if <new_datat> is 0 length
                    lprint ('! ignore: no date found (%s)' % file, False)
                    lprint ('! skipping null date: %s' % c['uri'], False)   # Show file (only) for readability
                    skipped.append('Null date: ' + data['uri'] + c['uri'])
                else:
            # I think we should be using the lastModified date instead of created date. Some files have a
            # created date of years ago (maven-metadata.xml) while its lastModified date is within a day of current date
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

    # Process that list into dictionary key value pairs and populate a new dict (to return)
    for x in data:
        x = x.strip()   # Remove trailing white space and new line char

        # There should only be two values (key|value). If there are more or less that's an issue, so skip it
        if len(x.split('|')) != 2:
            lprint ('  "%s" does not have two fields (%d) .. skipping' % (x, len(x.split('|'))), False)
            continue

        (k, v) = x.split("|")   # Get key value pair
        dct[k] = v              # Save in a dictionary

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
    with open(file, 'w') as fo:                 # Open <file> for 'write'
        for k in sorted(dct):                   # Loop through dictionary <dct>
            fo.write('%s|%s\n' % (k, dct[k]))   # Write file name | timestamp

def write_list(file, lst):
    """ Write a list <lst> of files to keep/skip/delete to file <file> """

    lprint ('Writing list to %s' % file, False)
    with open(file, 'w') as file_ptr:
        # I sort the list when I write so I can't merely insert these comments into the list. I must write them to
        # the file then write the sorted data behind it.
        if file == SKIPPED_FILES:
            file_ptr.write("%s\n" % HEADER2)
            file_ptr.write("#    These files were skipped (will not be removed) due to one or more of the following ..\n")
            file_ptr.write("#    1) There was an issue processing the file date\n")
            file_ptr.write("#    2) The file matches one of these names..\n")
            tmp = '#         {}'.format(', '.join(SKIP_FILES))
            file_ptr.write('%s\n' % tmp)
            file_ptr.write("#\n   # 3) The folder matches one of these names..\n")
            tmp = '#         {}'.format(', '.join(SKIP_FOLDERS))
            file_ptr.write('%s\n' % tmp)
            file_ptr.write("#\n%s\n" % HEADER2)
        elif file == KEEP_FILES:
            file_ptr.write("%s\n" % HEADER2)
            file_ptr.write("#    These files are < %d days old (will be kept)\n" % MAX_DAYS)
            file_ptr.write("%s\n" % HEADER2)
        elif file == DELETE_FILES:
            file_ptr.write("%s\n" % HEADER2)
            file_ptr.write("#    These files are marked for deletion because they are not in a skip lists and are > %d days old\n" % MAX_DAYS)
            file_ptr.write("%s\n" % HEADER2)

        for k in sorted(lst):
            file_ptr.write('%s\n' % k)

def delete_files(lst, u, p):
    """ Delete the files obtained from the delete list.
        See: https://en.wikipedia.org/wiki/List_of_HTTP_status_codes   for return codes
    """
    user_skip = False

    lprint('%d files to delete ..' % len(lst), False)

    for file in lst:                # Loop through list <lst> of files to marked for deletion
        if file.startswith('#'):    # Skip any comments in the file
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
            elif DELETE_ONE:   # Delete the first file listed and return. If delete fails try the next one
                lprint ('deleteing "%s"' % file, False)
                resp = requests.delete(file, auth=(u, p))
                if 200 <= resp.status_code <= 299:  # Success values (200-299)
#                    lprint ('Success: status code: %d' % resp.status_code, True)
                    return  # return if a file was deleted
                else:
                    lprint ('* Fail: status code: %d' % resp.status_code, True)

            else:           # Not interacive, just delete files as they come
                lprint ('deleteing "%s"' % file, False)
                resp = requests.delete(file, auth=(u, p))

        else:           # Delete option is not set. Run a simple "get" so we can test a transaction
            lprint ('"get" "%s"' % file, False)
            resp = requests.get(file, auth=(u, p))

        # Display the 'request' return value unless the user skipped that file (thus no status to report)
        if user_skip == False:
            if not 200 <= resp.status_code <= 299:  # Success values (200-299)
                lprint ('* Fail: status code: %d' % resp.status_code, False)
#            else:
#                lprint ('Success: status code: %d' % resp.status_code, False)

def parse_options():
    """ Parse options that are set in the environment (from Jenkins) """

    global VERBOSE, SKIP_FOLDERS, MAX_DAYS, CLEAN, DO_DELETE, DELETE_ONE, MAX_FILES_TO_COLLECT

    tmp = os.getenv("VERBOSE")
    if tmp and tmp.lower() in ['true', '1']:
        VERBOSE = True

    tmp = os.getenv("DELETE_ONE")               # Used as a test. Run scans but exit after deleting one file
    if tmp and tmp.lower() in ['true', '1']:
        DELETE_ONE = True

    tmp = os.getenv("DO_DELETE")                # Process the files marked for deletion
    if tmp and tmp.lower() in ['true', '1']:
        DO_DELETE = True

    tmp = os.getenv("KEEP_FILES")               # Don't remove the files generated
    if tmp and tmp.lower() in ['true', '1']:
        CLEAN = False

    tmp = os.getenv("USE_CREATED_TIME")         # Compare 'created' timestamp instead of 'lastModified'
    if tmp and tmp.lower() in ['true', '1']:
        USE_CREATED_TIME = True
        USE_MODIFIED_DATE = False

    tmp = os.getenv("SKIP_FOLDERS")             # Append new folders to skip to the default list
    if tmp:
        SKIP_FOLDERS.extend(tmp.split(','))     # Extend the list with comma sep string

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

    if LOG_DATA:                    # If set keep a recoed of what we did (even if NOT in verbose mode)
        with open(LOG_FILE, 'a') as lf:
            lf.write(msg + '\n')

    if VERBOSE:                     # Verbose is set
        if wait:                    # And requested user intervention
            if WAIT:                # And the WAIT option was issued
                raw_input(msg)      # Display message and wait for user
            else:                   # WAIT not issued; so display the message and wait a few seconds
                print msg           # Show message
                sys.stdout.flush()  # Make sure we flush the msg before sleeping
                time.sleep(2)       # And delay (instead of wait)
        else:
            print msg               # lprint did not request user input (to wait) so just print message
    else:
        if re.match(r'\* Warning', msg, re.IGNORECASE):  # If we have a warning to show, do it even if verbose not set
            print msg
    sys.stdout.flush()              # One final flush to make sure output is seen


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

    if args.delete_one:                 # CLI flag to delete file one and exit
        os.environ["DELETE_ONE"] = "1"  # Set same flag as envvar
        os.environ["DO_DELETE"]  = "1"  # If we want to delete one we must make sure this option is set too
    if args.delete:                     # CLI flag to delete files
        os.environ["DO_DELETE"] = "1"   # Set same flag as envvar
    if args.verbose:
        os.environ["VERBOSE"] = "1"
    if args.keep_file:                  # CLI flag to keep files
        os.environ["KEEP_FILES"] = "1"  # Set same flag as envvar
    if args.create_time:
        os.environ["USE_CREATED_TIME"] = "1"    # Set same flag as envvar
    if args.wait:
        WAIT = True
    if args.generate:
        GEN_SAVED_DATA = False  # Rely only on saved file data (for debugging)
    if args.skip:               # Add these CLI items to our skip list
        SKIP_FOLDERS.extend(args.skip.split(','))
        if not GEN_SAVED_DATA:
            print 'Warning: "-S" should not be used with "-g" (since we will be generating new data)'

    parse_options()     # Parse any env-var options Jenkins sent

    # If these files were obtained via an OS call the timestamps do not reflect what Artifactory will show.
    # So, to be safe, exit
    if FROM_OS == True and DO_DELETE == True:
        lprint('** Warning: These files have the system/OS timestamp and that WILL be different from the Artifactory timestamp!', False)
        lprint('Aborting', False)
        sys.exit(0)

    if DO_DELETE:
        lprint ('** Delete option is set **', True) # One last warning to show files will be removed
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
    print 'Script is running..'

    # I could process the data w/o saving it but the data is useful for debugging and running multiple times
    # without having to constantly send requests to artifactory (especially since this takes a VERY long time)
    if GEN_SAVED_DATA:  # Scan the artifactory folders and save the data for re-use
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

        if FROM_OS:   # Date is formatted differently
            tmp           = snap_catalog[dev_file]    # Strip off timezone
            file_dt       = datetime.datetime.strptime(tmp, '%a %b %d %H:%M:%S %Y') # Convert dev_file to datetime obj
            file_date_str = datetime.datetime.strftime(file_dt, '%Y-%m-%d')              # Create a 'date' (only) string
            file_date     = datetime.datetime.strptime(file_date_str, '%Y-%m-%d')        # Create a 'date' (only) object
        else:
            tmp           = re.search(r'(.*)(-\d{2,}:\d{2,})', snap_catalog[dev_file])   # Strip off timezone
            tmp_time      = tmp.groups()[0]                                              # Save string w/o TZ
            file_dt       = datetime.datetime.strptime(tmp_time, '%Y-%m-%dT%H:%M:%S.%f') # Convert dev_file to datetime obj
            file_date_str = datetime.datetime.strftime(file_dt, '%Y-%m-%d')              # Create a 'date' (only) string
            file_date     = datetime.datetime.strptime(file_date_str, '%Y-%m-%d')        # Create a 'date' (only) object

        delta         = todays_date - file_date

        if delta.days > MAX_DAYS:
            lprint ('  -> file is %d days old (%d is cutoff) .. marked for removal' % (delta.days, MAX_DAYS), False)
            delete.append(dev_file)         # File is ripe for picking .. put file in delete list
        else:
            lprint ('  -> file is only %d days old (%d is cutoff) .. file kept' % (delta.days, MAX_DAYS), False)
            keep.append(dev_file)           # File is to young

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
