Note that the db schema should be adjusted so that we can distinguish a file dependency if it's a test file (see below why it's useful). 

The existing data should contain the commit ID of that run.

For two entities we maintain a list of globally unique deterministic identifiers:
external packages
files that are git-tracked at HEAD, at project root

In the beginning, if we have existing data:
run <- the most recent run with the same python version.

get the commit of that run

We use git to find what has changed since the last commit compared to HEAD.
Files are either:
-created (git_new)
-modified (git_mod)
-removed (git_del)

git_del+git_mod: are  
for these files, find from the database which we have as files there. These are potentially modified file dependencies (some of those files in git mod and del might not have been tracked in the first place, this is the reason for this). We keep those that were tracked previously. From them, for the .py ones, compute the checksum, and disregard the ones with unchanged checksum, but first update their db entry with their new checksum (after stripping docstrings, like we do now).

git_new+git_mod: this is the set of files that our Dependency Tracker should expect and ignore all others (should sign reduce its overhead too!). Tracker should work with two subsets: the .py only (for imports) and all files for reads (one could read .py files too).



We consider external modules in this run and their versions. We use the existing data to compare and find external packages which can either be:
-added (pack_add)
-changed (their version changed) (pack_changed)
-removed (pack_rm)
pack_rm+pack_changed: pack_affecting (the packages we should consider to affect our existing dependency data).
pack_changed+pack_add: this is the set of external modules that our Dependency Tracker should expect and ignore all others (should sign reduce its overhead too!)



Now we're left with a set of files that 1. we had tracked before and 2. have meaningfully changed. This is the git_affected file set.

Using git_affected and pack_affecting, query the db to find their individual test dependents. That is, tests that either have any of git_affected as a dependency, or used a package of pack_affecting. We add to that any failed tests we noted in the db. this is the min_selected_tests. These are the tests we know should at least be selected.

Using min_selected_tests query the db to get their test files (this is why we should distinguish). These are the minimum test files that pytest should collect. These are min_collected_files (test files).
Using min_collected_files, query the db to find past test files that aren't in min_collected_files. These are explicitly_nocollect_files. These are test files pytest should NOT collect.

Now, use min_collected_files to query the db to find the tests that aren't in min_selected_tests. These are the explicitly deselected tests pytest needs to know. these are only the tests needed to be deselected for test files that are actually collected.

Important to note: all files which we have computed their checksum once, it should remain so.


Now, pytest collects at least those files. For each test file, it collects individual tests. It know which to forcefully select and deselect. Any other tests are selected, as they're new.

Now, after each test finishes, we get from the dependency tracker for each of them: files it read, local python files it imported and external packages it used. After all tests of a test file finish (i.e the point in pytest after executing the tests of a test file), we have this information for each test. [Note that these are all either new or modified. No existing file or package would be here because our dependency tracker knew which files to actually track].
For the failed tests we don't use their dep info, we just mark them as failed.
For the others, we establish their common deps (intersection for each kind), and their disagreements (union minus the intersection, for each kind that is).
So we have, after execution of tests of a test file, after it finishes:

common for all succeeded tests of that test file:
-py files
-other files read
-external packages and their version


unique for each succeeded test:
-py files
-other files read
-external packages and their version

We update this information to the database. We should only have to get hash of files ONCE and cache it for all other times we've finished for a test file. We should also only have to calculate the checksum for NEW .py files (because for the modified ones we should have computed it at the start). So for all other files we should normally only have to update the test dep info.

# IN CASE OF NO EXISTING DATA

Before any collection begins, we consider the files at HEAD using git (disregarding unstaged or uncommited states or files). 
From those we have two subsets: possible_imports (the .py files) and possible_reads (all files, including .py because one could read a .py file)

These are the files Import Tracker should consider for file reads and imports and only them
We also capture the current external packages and their versions. These are the external imports Import Tracker should consider and them only (excluding the local ones that were pip installed).



Now, python collects all files as normal.
After each test finishes, we get from the dependency tracker for each of them: files it read, local python files it imported and external packages it used. After all tests of a test file finish (i.e the point in pytest after executing the tests of a test file), we have this information for each test. [Note that these are all files that fit our constrains because the Tracker was set up so. Meaning they're all project files].
For the failed tests we don't use their dep info, we just mark them as failed.
For the others, we establish their common deps (intersection for each kind), and their disagreements (union minus the intersection, for each kind that is).
So we have, after execution of tests of a test file, after it finishes:

common for all succeeded tests of that test file:
-py files
-other files read
-external packages and their version


unique for each succeeded test:
-py files
-other files read
-external packages and their version

We update this information to the database. We should only have to get the hash of files, and calculate the checksum for .py files ONLY ONCE because we should cache these for all other test files. So normally, for most files we should only have to update the test dep info (because we've probably dealt with their hash or checksum in this run. 