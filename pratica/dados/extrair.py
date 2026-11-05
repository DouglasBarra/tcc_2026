import py7zr, sys, time
t=time.time()
with py7zr.SevenZipFile('StackOverflow2010.7z') as z:
    for i in z.list(): print(i.filename, i.uncompressed, i.is_directory)
    z.extractall('extraido')
print('segundos', round(time.time()-t))
