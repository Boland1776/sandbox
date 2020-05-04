This script is typically called via Jenkins pipeline (see the Jenkinsfile).

The pipeline has a few options (parameters) and the CLI has a few more.

MAX_DAYS: Scan files older than MAX_DAYS; default is 30
VERBOSE: Show the processing results as they happen (the log shows pretty much the same thing)
SKIP_LIST: The scipt is hardcoded to ignore certain folders/files. You cannot add to the skipped file list
via Jenkins (must be done in the script), but the folders to skip can be appended to the internal
list by adding a comma delimited list of folder names (no spaces at all)
DO_DELETE: When this box is checked the files marked for removal will be deleted (after the scan is complete). If
this box is NOT checked then a "get" request is performed (for functionality).
KEEP_FILE: When checked, all the files (shoiwn below) will be kept. When unchecked, all but the log will be removed.

This pipeline/script compares folders/files in the npm-dev repo with those in the npm-release repo.

If the file in the dev repo is in the release repo, it is kept.
If the file is NOT in the release repo, not in one of the SKIP_LISTS (note 1), and is > MAX_DAY old, it is marked for removal.
After scanning all the files, the ones marked for removal are deleted (note 2) from the artifactory npm-dev repo.
** See note 3

During the scan a few files are created; (but are deleted unless the KEEP_FILES box is checked)

release_catalog.txt
dev_catalog.txt
skipped.txt
keepers.txt
deleters.txt
log-.txt



Note 1:
Currently, the script skipd these folders;
'.npm/@bullhorn-internal', '.npm/@bullhorn', '.npm/bh-elements', '.npm/symphony-staffing', '.npm/chomsky',
'.npm/galaxy-parser', '.npm-generator-novo' '@bullhorn-internal', '@bullhorn', 'bh-elements', 'symphony-staffing',
'chomsky', 'galaxy-parser', 'generator-novo'
And any file name that contains DO_NOT _DELETE (or anything similar)

Note 2:
Files are stored locally on devartifactory1 , under /art-backups/current/repositories/npm-dev/

Note 3:
The files removed via this pipeline/script are removed in the artifactory. Artifactory, it seems, only "marks" the
files as deleted and puts them in a trash can. The files ARE NOT removed from the local filesystem until "Garbage Collection"
is actually performed (as of this writing, every Saturday at midnight).
