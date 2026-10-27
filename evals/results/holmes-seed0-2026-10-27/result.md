| injected | reported | TP | FP | FN | precision | recall | F1 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 20 | 11 | 8 | 2 | 12 | 0.80 | 0.40 | 0.53 |

1 other contradiction(s) quote a planted sentence for a different reason; counted neither as hits nor as false positives:
- Jefferson Hope dies in A Study in Scarlet · THE CONCLUSION. but acts in The Sign of the Four · The Great Agra Treasure

Misses by stage: extraction 8, judgement 4

| planted | lost at | detail |
|---|---|---|
| Mary Morstan.eye_color → 'hazel' (06-the-return-of-sherlock-holmes) | extraction | no fact was extracted from the planted sentence |
| Mycroft Holmes.eye_color → 'brown' (05-the-hound-of-the-baskervilles) | extraction | no fact was extracted from the planted sentence |
| John Watson.birthplace → 'Norwich' (05-the-hound-of-the-baskervilles) | judgement | ruled not_conflict: The bible mislabeled Watson's military deployment location as his birthplace. Watson served in Afghanistan during the second Afghan war and was returning from t |
| James Mortimer.eye_color → 'hazel' (06-the-return-of-sherlock-holmes) | extraction | no fact was extracted from the planted sentence |
| Joseph Stangerson.first_name → 'William' (02-the-sign-of-the-four) | extraction | no fact was extracted from the planted sentence |
| James McCarthy.first_name → 'Henry' (05-the-hound-of-the-baskervilles) | extraction | no fact was extracted from the planted sentence |
| Jonathan Small.wound_location → 'hand' (05-the-hound-of-the-baskervilles) | judgement | ruled not_conflict: Jonathan Small has two separate physical injuries described in the text: an amputated right leg (leaving a wooden stump on his right side) and missing skin/scar |
| Toby.hair_color → 'grey' (03-the-adventures-of-sherlock-holmes) | extraction | no fact was extracted from the planted sentence |
| Sherlock Holmes.eye_color → 'blue' (03-the-adventures-of-sherlock-holmes) | extraction | no fact was extracted from the planted sentence |
| Joseph Harrison.first_name → 'Edward' (06-the-return-of-sherlock-holmes) | extraction | no fact was extracted from the planted sentence |
| Jefferson Hope.birthplace → 'Edinburgh' (02-the-sign-of-the-four) | judgement | ruled not_conflict: The bible misattributed a statement made by Jonathan Small in The Sign of the Four ("I was born in Edinburgh") to Jefferson Hope, a character from A Study in Sc |
| Arthur Pinner.eye_color → 'brown' (05-the-hound-of-the-baskervilles) | judgement | ruled ambiguous: Arthur Pinner is described as having 'dark' eyes in one text and 'brown' eyes in another. Since brown eyes are dark, the two descriptions are loose enough to be |
