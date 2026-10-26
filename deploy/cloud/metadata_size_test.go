package models

import (
	"io"
	"testing"
)

func TestMetadataRenameUpdatesSchemaByteSizes(t *testing.T) {
	doc := NewHashDocWithMeta("sample", MetadataFile{DocumentName: "Short"})
	doc.AddFile(NewHashEntry("old", "sample.metadata", 12))
	doc.AddFile(NewHashEntry("payload", "sample.pdf", 1000))
	for _, name := range []string{"Очень длинное кириллическое название", "Short", ""} {
		doc.DocumentName = name
		_, reader, err := doc.MetadataReader()
		if err != nil { t.Fatal(err) }
		data, err := io.ReadAll(reader)
		if err != nil { t.Fatal(err) }
		if doc.Files[0].Size != int64(len(data)) || doc.Size != 1000+int64(len(data)) {
			t.Fatalf("schema size mismatch: file=%d doc=%d actual=%d", doc.Files[0].Size, doc.Size, len(data))
		}
	}
}
