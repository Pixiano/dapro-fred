# train_classifier.py
import pickle
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from intents import intents

# Prepare data
X = []  # phrases
y = []  # labels

for intent, examples in intents.items():
    for ex in examples:
        X.append(ex)
        y.append(intent)

# Feature extraction
vectorizer = TfidfVectorizer()
X_vec = vectorizer.fit_transform(X)

# Train classifier
clf = LogisticRegression()
clf.fit(X_vec, y)

# Save model and vectorizer
with open("classifier.pkl", "wb") as f:
    pickle.dump({"model": clf, "vectorizer": vectorizer}, f)

print("Training complete. Model saved as classifier.pkl")