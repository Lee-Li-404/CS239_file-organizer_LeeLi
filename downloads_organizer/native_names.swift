// Optional on-device PERSON detector for macOS. No networking or logging.
// Compile: swiftc native_names.swift -o local_names
import Foundation
import NaturalLanguage

let input = FileHandle.standardInput.readDataToEndOfFile()
do {
    let value = try JSONSerialization.jsonObject(with: input) as? [String: String]
    guard let text = value?["text"] else { exit(2) }
    let tagger = NLTagger(tagSchemes: [.nameType])
    tagger.string = text
    var names = Set<String>()
    tagger.enumerateTags(in: text.startIndex..<text.endIndex, unit: .word,
                         scheme: .nameType,
                         options: [.omitPunctuation, .omitWhitespace, .joinNames]) { tag, range in
        if tag == .personalName { names.insert(String(text[range])) }
        return true
    }
    let output = try JSONSerialization.data(withJSONObject: names.sorted())
    FileHandle.standardOutput.write(output)
} catch {
    exit(2)
}
