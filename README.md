# Kling AI Movie Studio (prototype)

A Streamlit storyboard editor with Kling text-to-video and image-to-video generation.

## Upload to GitHub
Upload **app.py** and **requirements.txt** to the ROOT of your existing repository, not the ZIP. You may keep your existing README and .gitignore. Never upload actual API credentials.

## Run locally
`pip install -r requirements.txt`
`streamlit run app.py`

## Configure credentials
The Kling developer portal normally provides an Access Key and Secret Key. These are used to create a short-lived JWT on the server. Set `KLING_ACCESS_KEY` and `KLING_SECRET_KEY` as environment variables or Streamlit hosting secrets. Never paste them into GitHub files, screenshots, or chat.

## Phone deployment notes
A GitHub repo alone does not run the application. A separate Streamlit-compatible host is needed. Streamlit Community Cloud can connect to a GitHub repository, but verify its access and privacy controls: **a private repo does not mean the deployed app is private**. Use hosting with login/access restrictions and spending safeguards before enabling paid generation.

## Limitations
This is an initial prototype, not a tested live Kling deployment. Confirm your API's model IDs, available features, pricing, and accepted image formats before paid use. Image-to-video model support and aspect ratios can differ. Videos are returned as temporary Kling URLs; save promptly. Storyboard changes live in the Streamlit session; export JSON often. No database, queue, account authentication, or automatic budget caps are included.
