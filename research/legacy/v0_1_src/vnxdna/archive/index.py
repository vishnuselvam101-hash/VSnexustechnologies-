def find_file(archive:dict,file_id:str)->dict:
 for item in archive.get('files',[]):
  if item['manifest']['file_id']==file_id:return item
 raise KeyError(f"File ID not found: {file_id}")
