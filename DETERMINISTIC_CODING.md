both controller and worker consider the files

the core idea is to construct a method for both controller and worker to encode the same file with the same code deterministicly without coordinating first.
Each file and package name has a unique number.
All packages are listed alphabetically, so a package 1 is the first package in that sorted list.
Files follow the same intuition, but per directory. 
Meaning, if we're at the project root and we want to encode file f, then the encoding is simply the index of f when we sort the immediate childs of f (including directories). let's say that index is i. if f happens to be a directory, then its contents work in the same way, but with the i prefix and so on.

Here's an example of an encoded directory and all its contents:

1
2/
	21
	22
3
4/
	41/
		411
		412
		413
	42
5

It is CRUCIAL to consider that we ONLY consider git tracked files at HEAD. that is to ensure a common base for all workers.

Workers use this encoding for ALL file and packages references. we only deserialize when we actually need to.